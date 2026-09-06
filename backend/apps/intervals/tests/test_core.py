import pytest
import respx
from django.utils import timezone
from httpx import Response

from apps.charts.renderer import render_intervals_chart, steps_to_intervals
from apps.intervals.client import IntervalsClient
from apps.intervals.models import Activity, CalendarEventCache
from apps.intervals.services import (
    extract_athlete_ftp,
    extract_compliance,
    extract_form_fields,
    plan_payload,
    recent_reports_payload,
    sync_calendar,
    sync_form,
    upsert_activity_from_detail,
)
from apps.notifications.models import NotificationLog
from apps.notifications.services import create_log_or_skip
from apps.users.crypto import decrypt_value, encrypt_value, mask_secret
from apps.users.models import IntervalsCredentials, NotificationSettings, TelegramUser


@pytest.mark.django_db
def test_encrypt_decrypt_roundtrip():
    token = encrypt_value("secret-api-key")
    assert token != "secret-api-key"
    assert decrypt_value(token) == "secret-api-key"
    assert mask_secret("abcdefghij") == "******ghij"


def test_extract_form_fields():
    row = {"icu_ctl": 55.2, "icu_atl": 40.1, "icu_form": 15.1, "weight": 72.5, "vo2max": 52}
    fields = extract_form_fields(row)
    assert fields["fitness"] == 55.2
    assert fields["fatigue"] == 40.1
    assert fields["form"] == 15.1
    assert fields["weight"] == 72.5
    assert fields["vo2max"] == 52


def test_extract_form_fields_computes_tsb():
    row = {"ctl": 80.96, "atl": 79.41, "weight": None, "vo2max": None}
    fields = extract_form_fields(row)
    assert fields["form"] == round(80.96 - 79.41, 5)


def test_merge_form_fields_uses_history_and_athlete():
    from apps.intervals.services import _merge_form_fields

    rows = [
        {"id": "2026-09-06", "ctl": 81.0, "atl": 79.0, "weight": None, "vo2max": None},
        {"id": "2026-09-04", "ctl": 80.0, "atl": 78.0, "weight": None, "vo2max": 62.0},
        {"id": "2026-08-29", "ctl": 79.0, "atl": 77.0, "weight": 77.8, "vo2max": None},
    ]
    fields = _merge_form_fields(rows, {"icu_weight": 77.8})
    assert fields["form"] == round(81.0 - 79.0, 5)
    assert fields["vo2max"] == 62.0
    assert fields["weight"] == 77.8


def test_extract_compliance_scales_fraction():
    assert extract_compliance({"icu_compliance": 0.87}) == 87.0
    assert extract_compliance({"icu_intensity": 92}) == 92.0


@respx.mock
@pytest.mark.django_db
def test_intervals_client_get_athlete():
    respx.get("https://intervals.icu/api/v1/athlete/0").mock(
        return_value=Response(200, json={"id": "i123", "name": "Test"})
    )
    client = IntervalsClient(api_key="test-key", base_url="https://intervals.icu/api/v1")
    data = client.get_athlete()
    assert data["id"] == "i123"


@respx.mock
@pytest.mark.django_db
def test_sync_form_and_calendar(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    user = TelegramUser.objects.create(telegram_id=1, username="u")
    creds = IntervalsCredentials(user=user)
    creds.set_api_key("test-key")
    creds.is_valid = True
    creds.save()

    today = timezone.localdate().isoformat()
    respx.get("https://intervals.icu/api/v1/athlete/0/wellness").mock(
        return_value=Response(
            200,
            json=[{"id": today, "icu_ctl": 60, "icu_atl": 50, "icu_form": 10, "weight": 70}],
        )
    )
    respx.get("https://intervals.icu/api/v1/athlete/0").mock(
        return_value=Response(200, json={"id": "i123", "icu_weight": 70})
    )
    respx.get("https://intervals.icu/api/v1/athlete/0/events").mock(
        return_value=Response(
            200,
            json=[
                {
                    "id": 101,
                    "category": "WORKOUT",
                    "name": "Sweet Spot",
                    "type": "Ride",
                    "start_date_local": f"{today}T00:00:00",
                    "icu_training_load": 80,
                    "workout_doc": {"duration": 3600, "steps": []},
                }
            ],
        )
    )

    snap = sync_form(user)
    assert snap.fitness == 60
    count = sync_calendar(user)
    assert count == 1
    assert user.calendar_events.count() == 1


@pytest.mark.django_db
def test_notification_idempotency():
    user = TelegramUser.objects.create(telegram_id=2)
    first = create_log_or_skip(user, NotificationLog.Kind.ANNOUNCE, "evt:2026-01-01")
    second = create_log_or_skip(user, NotificationLog.Kind.ANNOUNCE, "evt:2026-01-01")
    assert first is not None
    assert second is None


@pytest.mark.django_db
def test_upsert_activity_and_chart(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    user = TelegramUser.objects.create(telegram_id=3)
    detail = {
        "id": "a1",
        "name": "Morning Ride",
        "type": "Ride",
        "start_date_local": "2026-09-06T08:00:00",
        "moving_time": 3600,
        "icu_training_load": 90,
        "icu_intensity": 85,
        "icu_compliance": 0.91,
        "icu_intervals": [
            {"type": "WORK", "moving_time": 300, "intensity": 110, "zone": 4},
            {"type": "RECOVERY", "moving_time": 120, "intensity": 50, "zone": 1},
        ],
    }
    activity = upsert_activity_from_detail(user, detail)
    assert activity.compliance == 91.0
    path = render_intervals_chart(activity.external_id, activity.intervals_json, title="Test")
    assert (tmp_path / path).exists()


def test_steps_to_intervals_ftp_and_reps():
    doc = {
        "ftp": 200,
        "steps": [
            {"duration": 300, "power": {"units": "%ftp", "value": 50}, "zone": 2},
            {
                "reps": 2,
                "steps": [
                    {"duration": 60, "power": {"units": "w", "value": 250}, "zone": 4},
                    {"duration": 120, "power": {"units": "%ftp", "start": 40, "end": 50}},
                ],
            },
        ]
    }
    intervals = steps_to_intervals(doc, ftp=200)
    assert len(intervals) == 5  # 1 + 2*(2)
    assert intervals[0]["intensity"] == 50.0  # %FTP
    assert intervals[1]["intensity"] == 125.0  # 250W / 200 FTP * 100
    assert intervals[2]["intensity"] == 45.0  # mid 45% FTP


def test_render_prefers_intensity_and_minutes(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    intervals = [
        {"type": "WORK", "moving_time": 600, "intensity": 110, "average_watts": 220, "zone": 4},
        {"type": "RECOVERY", "moving_time": 300, "intensity": 50, "average_watts": 120, "zone": 1},
    ]
    path = render_intervals_chart("intensity_test", intervals, title="Intensity")
    assert (tmp_path / path).exists()


def test_format_workout_steps():
    from apps.notifications.services import format_workout_steps

    doc = {
        "steps": [
            {"text": "Разминка", "duration": 600, "power": {"units": "power_zone", "value": 1}},
            {
                "reps": 5,
                "steps": [
                    {"duration": 300, "power": {"units": "%ftp", "start": 102, "end": 110}},
                    {"duration": 300, "power": {"units": "power_zone", "value": 1}},
                ],
            },
        ]
    }
    text = format_workout_steps(doc)
    assert "Разминка" in text
    assert "5×" in text
    assert "% FTP" in text
    assert "Z1" in text


def test_extract_athlete_ftp():
    assert extract_athlete_ftp({"icu_ftp": 250}) == 250.0
    assert extract_athlete_ftp({"sportSettings": [{"ftp": 280}]}) == 280.0
    assert extract_athlete_ftp({}) is None


@respx.mock
@pytest.mark.django_db
def test_plan_payload_and_recent_reports_api(client, settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    settings.INTERNAL_API_TOKEN = "test-internal-token"
    headers = {"HTTP_X_INTERNAL_TOKEN": "test-internal-token"}

    user = TelegramUser.objects.create(telegram_id=77, username="p")
    creds = IntervalsCredentials(user=user)
    creds.set_api_key("test-key")
    creds.is_valid = True
    creds.save()

    today = timezone.localdate().isoformat()
    CalendarEventCache.objects.create(
        user=user,
        external_id="evt1",
        category="WORKOUT",
        name="Sweet Spot",
        type="Ride",
        start_date_local=timezone.now(),
        icu_training_load=80,
        workout_doc={
            "duration": 1800,
            "steps": [
                {"duration": 600, "power": {"units": "w", "value": 200}, "zone": 3},
                {"duration": 300, "power": {"units": "w", "value": 100}, "zone": 1},
            ],
        },
    )
    respx.get("https://intervals.icu/api/v1/athlete/0").mock(
        return_value=Response(200, json={"id": "i123", "icu_ftp": 250})
    )

    payload = plan_payload(user, with_charts=True)
    assert payload["workouts"]
    assert payload["workouts"][0]["chart_path"]
    assert (tmp_path / payload["workouts"][0]["chart_path"]).exists()

    Activity.objects.create(
        user=user,
        external_id="act99",
        name="Evening Ride",
        type="Ride",
        start_date_local=timezone.now(),
        intervals_json=[
            {"type": "WORK", "moving_time": 300, "average_watts": 210, "zone": 4},
        ],
    )
    recent = recent_reports_payload(user, limit=5)
    assert len(recent["items"]) == 1
    assert recent["items"][0]["chart_path"]
    assert recent["items"][0]["caption"]

    resp = client.get("/api/v1/bot/users/77/reports/recent?limit=5", **headers)
    assert resp.status_code == 200
    assert len(resp.json()["items"]) == 1

    chart_rel = recent["items"][0]["chart_path"]
    resp = client.get(f"/api/v1/bot/media/{chart_rel}", **headers)
    assert resp.status_code == 200
    assert resp["Content-Type"] == "image/png"

    resp = client.get("/api/v1/bot/users/77/plan", **headers)
    assert resp.status_code == 200
    assert "workouts" in resp.json()


@pytest.mark.django_db
def test_api_upsert_and_settings(client, settings):
    settings.INTERNAL_API_TOKEN = "test-internal-token"
    headers = {"HTTP_X_INTERNAL_TOKEN": "test-internal-token"}
    resp = client.post(
        "/api/v1/bot/users/upsert",
        data={"telegram_id": 42, "username": "athlete"},
        content_type="application/json",
        **headers,
    )
    assert resp.status_code == 200
    assert resp.json()["telegram_id"] == 42
    assert NotificationSettings.objects.filter(user__telegram_id=42).exists()

    resp = client.patch(
        "/api/v1/bot/users/42/settings",
        data={"announce_enabled": False, "announce_time": "07:15:00"},
        content_type="application/json",
        **headers,
    )
    assert resp.status_code == 200
    assert resp.json()["announce_enabled"] is False
