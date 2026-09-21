from datetime import date, time, timedelta
from unittest.mock import MagicMock, patch

import pytest
import respx
from django.utils import timezone
from httpx import Response

from apps.ai.analysis import (
    SYSTEM_PROMPT_DAY,
    SYSTEM_PROMPT_WEEK,
    _compact_activity,
    activity_environment,
    analyze_period,
    build_period_context,
    format_period_report_message,
    resolve_period_dates,
)
from apps.ai.client import DeepSeekClient, DeepSeekError
from apps.ai.models import AiPeriodReport
from apps.intervals.models import Activity, AthleteSnapshot, CalendarEventCache, WellnessDay
from apps.notifications.models import NotificationLog
from apps.notifications.tasks import send_activity_report, send_period_analysis
from apps.users.models import NotificationSettings, TelegramUser
from apps.users.subscriptions import grant_subscription


@pytest.fixture
def user(db):
    u = TelegramUser.objects.create(telegram_id=9001, username="ai_user")
    NotificationSettings.objects.create(
        user=u,
        report_enabled=True,
        period_analysis_enabled=True,
        analysis_time=time(21, 30),
    )
    return u


@pytest.fixture
def activity(user):
    event = CalendarEventCache.objects.create(
        user=user,
        external_id="ev1",
        name="Sweet Spot",
        type="Ride",
        category="WORKOUT",
        start_date_local=timezone.now(),
        icu_training_load=80,
        workout_doc={
            "steps": [
                {"name": "Warmup", "duration": 600, "power": {"units": "%ftp", "value": 55}},
            ]
        },
    )
    AthleteSnapshot.objects.create(
        user=user,
        fitness=70,
        fatigue=55,
        form=15,
        weight=75,
        vo2max=50,
        as_of_date=timezone.now().date(),
    )
    WellnessDay.objects.create(
        user=user,
        date=timezone.now().date(),
        sleep_secs=25200,
        resting_hr=48,
        hrv=65,
        weight=75.2,
        fatigue=2,
        injury=1,
        readiness=7,
        fitness=70,
        fatigue_atl=55,
        form=15,
    )
    return Activity.objects.create(
        user=user,
        external_id="act1",
        name="Morning Ride",
        type="Ride",
        start_date_local=timezone.now(),
        moving_time=3600,
        distance=40000,
        icu_training_load=75,
        icu_intensity=85,
        compliance=90,
        average_watts=200,
        matched_event=event,
        curve_prs=[
            {"metric": "power", "duration_sec": 300, "value": 320, "label": "5 мин"},
        ],
    )


def test_resolve_period_dates_day():
    start, end = resolve_period_dates("day", today=date(2026, 9, 20))
    assert start == end == date(2026, 9, 20)


def test_resolve_period_dates_week():
    # Saturday 2026-09-19 → Mon 14 … Sun 20
    start, end = resolve_period_dates("week", today=date(2026, 9, 19))
    assert start == date(2026, 9, 14)
    assert end == date(2026, 9, 20)


def test_build_period_context(activity):
    today = timezone.now().date()
    # Extra lookback day for trends
    WellnessDay.objects.create(
        user=activity.user,
        date=today - timedelta(days=3),
        sleep_secs=21600,
        resting_hr=50,
        hrv=60,
        weight=75.5,
        fitness=68,
        fatigue_atl=50,
        form=18,
    )
    ctx = build_period_context(activity.user, period_start=today, period_end=today)
    assert ctx["totals"]["completed_activities"] == 1
    assert ctx["activities"][0]["name"] == "Morning Ride"
    assert ctx["activities"][0]["environment"] == "outdoor"
    assert ctx["activities"][0]["compliance_note"] == "may_be_unreliable"
    assert ctx["activities"][0]["curve_prs"][0]["duration_sec"] == 300
    assert ctx["plan"][0]["name"] == "Sweet Spot"
    assert ctx["wellness"][0]["resting_hr"] == 48
    assert ctx["wellness"][0]["sleep_hours"] == 7.0
    assert ctx["form"]["fitness_ctl"] == 70
    assert ctx["totals"]["avg_sleep_hours"] == 7.0
    assert ctx["totals"]["avg_resting_hr"] == 48
    assert ctx["totals"]["avg_hrv"] == 65
    assert len(ctx["hrv_trend"]) >= 2
    assert len(ctx["resting_hr_trend"]) >= 2
    assert len(ctx["sleep_trend"]) >= 2
    assert ctx["form_trend"] is not None
    assert ctx["form_trend"]["end"]["fitness_ctl"] == 70
    assert ctx["personal_records"][0]["duration_sec"] == 300
    assert ctx["personal_records"][0]["activity_name"] == "Morning Ride"


def test_system_prompts_mention_compliance_and_environment():
    for prompt in (SYSTEM_PROMPT_DAY, SYSTEM_PROMPT_WEEK):
        assert "Compliance" in prompt or "compliance" in prompt
        assert "ненадёжен" in prompt
        assert "indoor" in prompt
        assert "outdoor" in prompt
        assert "станке" in prompt
        assert "CTL" in prompt
        assert "ATL" in prompt
        assert "HRV" in prompt
        assert "personal_records" in prompt or "curve_prs" in prompt
        assert "Form" in prompt


def test_activity_environment_trainer_true(user):
    activity = Activity.objects.create(
        user=user,
        external_id="indoor1",
        name="Trainer",
        type="Ride",
        raw_json={"trainer": True, "device_watts": True},
    )
    env, trainer = activity_environment(activity)
    assert env == "indoor"
    assert trainer is True
    compact = _compact_activity(activity)
    assert compact["environment"] == "indoor"
    assert compact["trainer"] is True
    assert compact["device_watts"] is True
    assert compact["compliance_note"] == "may_be_unreliable"


def test_activity_environment_virtual_ride(user):
    activity = Activity.objects.create(
        user=user,
        external_id="virt1",
        name="Zwift",
        type="VirtualRide",
        raw_json={},
    )
    env, trainer = activity_environment(activity)
    assert env == "indoor"
    assert trainer is None


def test_activity_environment_outdoor_ride(user):
    activity = Activity.objects.create(
        user=user,
        external_id="out1",
        name="Road",
        type="Ride",
        raw_json={"trainer": False},
    )
    env, trainer = activity_environment(activity)
    assert env == "outdoor"
    assert trainer is False


def test_format_period_report_message_escapes_html():
    text = format_period_report_message(
        "day",
        "A < B & C > D",
        period_start=date(2026, 9, 20),
        period_end=date(2026, 9, 20),
    )
    assert "&lt;" in text
    assert "&amp;" in text
    assert "Анализ дня" in text


@respx.mock
def test_deepseek_client_chat(settings):
    settings.DEEPSEEK_API_KEY = "sk-test"
    settings.DEEPSEEK_BASE_URL = "https://api.deepseek.com"
    settings.DEEPSEEK_MODEL = "deepseek-chat"
    respx.post("https://api.deepseek.com/chat/completions").mock(
        return_value=Response(
            200,
            json={
                "choices": [
                    {"message": {"content": "1. План\n2. Нагрузка\n3. Здоровье\n4. Выводы"}}
                ]
            },
        )
    )
    client = DeepSeekClient()
    result = client.chat([{"role": "user", "content": "hi"}])
    assert "План" in result


@respx.mock
def test_deepseek_client_error(settings):
    settings.DEEPSEEK_API_KEY = "sk-test"
    settings.DEEPSEEK_BASE_URL = "https://api.deepseek.com"
    respx.post("https://api.deepseek.com/chat/completions").mock(
        return_value=Response(500, text="boom")
    )
    client = DeepSeekClient()
    with pytest.raises(DeepSeekError):
        client.chat([{"role": "user", "content": "hi"}])


@pytest.mark.django_db
def test_analyze_period_without_api_key(activity, settings):
    settings.DEEPSEEK_API_KEY = ""
    today = timezone.now().date()
    text, ctx = analyze_period(
        activity.user, "day", period_start=today, period_end=today
    )
    assert text is None
    assert ctx["activities"]


@respx.mock
@pytest.mark.django_db
def test_analyze_period_success(activity, settings):
    settings.DEEPSEEK_API_KEY = "sk-test"
    settings.DEEPSEEK_BASE_URL = "https://api.deepseek.com"
    settings.DEEPSEEK_MODEL = "deepseek-chat"
    respx.post("https://api.deepseek.com/chat/completions").mock(
        return_value=Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": (
                                "1. План и факт\nОк.\n"
                                "2. Нагрузка и восстановление\nНорма.\n"
                                "3. Здоровье\nСон хороший.\n"
                                "4. Выводы на завтра\nОтдых."
                            )
                        }
                    }
                ]
            },
        )
    )
    today = timezone.now().date()
    text, _ = analyze_period(
        activity.user, "day", period_start=today, period_end=today
    )
    assert text is not None
    assert "Здоровье" in text


@pytest.mark.django_db
def test_send_activity_report_no_longer_calls_ai(activity, settings):
    settings.DEEPSEEK_API_KEY = "sk-test"
    settings.TELEGRAM_BOT_TOKEN = "tg-token"
    grant_subscription(activity.user)

    with (
        patch("apps.notifications.tasks.render_intervals_chart", return_value="charts/x.png"),
        patch("apps.notifications.tasks._tg_send_photo", return_value={"message_id": 1}),
        patch("apps.notifications.tasks.Path") as path_cls,
        patch("apps.ai.analysis.analyze_period") as analyze_mock,
        patch("apps.notifications.tasks.deliver_text") as deliver_mock,
    ):
        path_cls.return_value.__truediv__ = MagicMock(
            return_value=MagicMock(exists=MagicMock(return_value=True))
        )
        send_activity_report(activity.id)

    analyze_mock.assert_not_called()
    activity.refresh_from_db()
    assert activity.report_sent_at is not None
    deliver_mock.assert_not_called()
    assert NotificationLog.objects.filter(
        user=activity.user, kind=NotificationLog.Kind.REPORT, status=NotificationLog.Status.SENT
    ).exists()


@pytest.mark.django_db
def test_send_period_analysis_requires_subscription(activity, settings):
    settings.DEEPSEEK_API_KEY = "sk-test"
    today = timezone.now().date()
    with patch("apps.ai.analysis.analyze_period") as analyze_mock:
        send_period_analysis(activity.user.id, "day", today.isoformat())
    analyze_mock.assert_not_called()
    assert not AiPeriodReport.objects.filter(user=activity.user).exists()


@pytest.mark.django_db
def test_send_period_analysis_success(activity, settings):
    settings.DEEPSEEK_API_KEY = "sk-test"
    settings.TELEGRAM_BOT_TOKEN = "tg-token"
    grant_subscription(activity.user)
    today = timezone.now().date()

    with (
        patch("apps.intervals.services.sync_calendar"),
        patch("apps.intervals.services.sync_form"),
        patch("apps.intervals.services.poll_new_activities"),
        patch(
            "apps.ai.analysis.analyze_period",
            return_value=("1. План\n2. Нагрузка\n3. Здоровье\n4. Выводы", {"ok": True}),
        ) as analyze_mock,
        patch("apps.notifications.tasks.deliver_text", return_value=42) as deliver_mock,
    ):
        send_period_analysis(activity.user.id, "day", today.isoformat())

    analyze_mock.assert_called_once()
    deliver_mock.assert_called_once()
    assert "Анализ дня" in deliver_mock.call_args.args[1]
    report = AiPeriodReport.objects.get(user=activity.user, kind="day")
    assert report.status == AiPeriodReport.Status.READY
    assert report.summary.startswith("1. План")
    assert report.sent_at is not None
    assert NotificationLog.objects.filter(
        user=activity.user,
        kind=NotificationLog.Kind.DAY_ANALYSIS,
        status=NotificationLog.Status.SENT,
    ).exists()


@pytest.mark.django_db
def test_send_period_analysis_skips_duplicate(activity, settings):
    settings.TELEGRAM_BOT_TOKEN = "tg-token"
    grant_subscription(activity.user)
    today = timezone.now().date()
    AiPeriodReport.objects.create(
        user=activity.user,
        kind="day",
        period_start=today,
        period_end=today,
        summary="cached",
        status=AiPeriodReport.Status.READY,
        sent_at=timezone.now(),
    )
    with patch("apps.ai.analysis.analyze_period") as analyze_mock:
        send_period_analysis(activity.user.id, "day", today.isoformat())
    analyze_mock.assert_not_called()


@pytest.mark.django_db
def test_send_period_analysis_force_regenerates(activity, settings):
    settings.TELEGRAM_BOT_TOKEN = "tg-token"
    grant_subscription(activity.user)
    today = timezone.now().date()
    AiPeriodReport.objects.create(
        user=activity.user,
        kind="day",
        period_start=today,
        period_end=today,
        summary="old",
        status=AiPeriodReport.Status.READY,
        sent_at=timezone.now(),
    )
    with (
        patch("apps.intervals.services.sync_calendar"),
        patch("apps.intervals.services.sync_form"),
        patch("apps.intervals.services.poll_new_activities"),
        patch(
            "apps.ai.analysis.analyze_period",
            return_value=("new summary", {}),
        ) as analyze_mock,
        patch("apps.notifications.tasks.deliver_text", return_value=1),
    ):
        send_period_analysis(
            activity.user.id,
            "day",
            today.isoformat(),
            today.isoformat(),
            True,
            False,
            False,
        )
    analyze_mock.assert_called_once()
    report = AiPeriodReport.objects.get(user=activity.user, kind="day")
    assert report.summary == "new summary"


@pytest.mark.django_db
def test_send_period_analysis_resend_only(activity, settings):
    settings.TELEGRAM_BOT_TOKEN = "tg-token"
    grant_subscription(activity.user)
    today = timezone.now().date()
    AiPeriodReport.objects.create(
        user=activity.user,
        kind="day",
        period_start=today,
        period_end=today,
        summary="keep me",
        status=AiPeriodReport.Status.READY,
        sent_at=timezone.now() - timedelta(hours=1),
    )
    with (
        patch("apps.ai.analysis.analyze_period") as analyze_mock,
        patch("apps.notifications.tasks.deliver_text", return_value=9) as deliver_mock,
    ):
        send_period_analysis(
            activity.user.id,
            "day",
            today.isoformat(),
            today.isoformat(),
            False,
            True,
            True,
        )
    analyze_mock.assert_not_called()
    deliver_mock.assert_called_once()
    assert "keep me" in deliver_mock.call_args.args[1]
