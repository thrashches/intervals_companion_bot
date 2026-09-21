from unittest.mock import MagicMock

import pytest
import respx
from django.utils import timezone
from httpx import Response

from apps.intervals.curves import (
    detect_curve_prs,
    duration_label,
    extract_prs_from_curve,
)
from apps.intervals.models import Activity
from apps.intervals.services import poll_new_activities
from apps.notifications.services import format_activity_report
from apps.users.models import IntervalsCredentials, TelegramUser


def test_duration_label():
    assert duration_label(300) == "5 мин"
    assert duration_label(5) == "5 с"
    assert duration_label(60) == "1 мин"


def test_extract_prs_from_curve_power_5min():
    curve = {
        "secs": [5, 60, 300, 1200],
        "values": [900, 400, 320, 250],
        "activity_id": ["other", "act1", "act1", "other"],
    }
    prs = extract_prs_from_curve(curve, activity_id="act1", metric="power")
    assert len(prs) == 2
    assert prs[0] == {
        "metric": "power",
        "duration_sec": 60,
        "value": 400.0,
        "label": "1 мин",
    }
    assert prs[1]["duration_sec"] == 300
    assert prs[1]["value"] == 320.0
    assert prs[1]["label"] == "5 мин"


def test_extract_prs_ignores_non_key_durations():
    curve = {
        "secs": [7, 300],
        "values": [800, 310],
        "activity_id": ["act1", "act1"],
    }
    prs = extract_prs_from_curve(curve, activity_id="act1", metric="power")
    assert len(prs) == 1
    assert prs[0]["duration_sec"] == 300


def test_detect_curve_prs_uses_client(db):
    user = TelegramUser.objects.create(telegram_id=9100)
    activity = Activity.objects.create(
        user=user,
        external_id="act99",
        name="PR Ride",
        type="VirtualRide",
    )
    client = MagicMock()
    client.get_power_curves.return_value = {
        "list": [
            {
                "secs": [300],
                "values": [333],
                "activity_id": ["act99"],
            }
        ]
    }
    client.get_hr_curves.return_value = {
        "list": [
            {
                "secs": [60],
                "values": [180],
                "activity_id": ["act99"],
            }
        ]
    }
    prs = detect_curve_prs(client, activity)
    client.get_power_curves.assert_called_once_with(curves="all", activity_type="Ride")
    client.get_hr_curves.assert_called_once_with(curves="all", activity_type="Ride")
    assert prs[0]["metric"] == "power"
    assert prs[0]["duration_sec"] == 300
    assert prs[1]["metric"] == "hr"
    assert prs[1]["duration_sec"] == 60


def test_detect_curve_prs_survives_api_error(db):
    from apps.intervals.client import IntervalsAPIError

    user = TelegramUser.objects.create(telegram_id=9101)
    activity = Activity.objects.create(
        user=user, external_id="act_err", name="X", type="Ride"
    )
    client = MagicMock()
    client.get_power_curves.side_effect = IntervalsAPIError("boom", status_code=500)
    client.get_hr_curves.return_value = {}
    assert detect_curve_prs(client, activity) == []


def test_format_activity_report_includes_prs(db):
    user = TelegramUser.objects.create(telegram_id=9102)
    activity = Activity.objects.create(
        user=user,
        external_id="act_pr",
        name="Hard Ride",
        type="Ride",
        moving_time=3600,
        curve_prs=[
            {"metric": "power", "duration_sec": 300, "value": 320, "label": "5 мин"},
            {"metric": "hr", "duration_sec": 60, "value": 175, "label": "1 мин"},
        ],
    )
    text = format_activity_report(activity)
    assert "🏆 Новый рекорд: мощность 5 мин — 320 W" in text
    assert "🏆 Новый рекорд: пульс 1 мин — 175 bpm" in text


@pytest.mark.django_db
@respx.mock
def test_poll_new_activities_saves_curve_prs(settings):
    settings.INTERVALS_API_BASE = "https://intervals.icu/api/v1"
    user = TelegramUser.objects.create(telegram_id=9103)
    creds = IntervalsCredentials(user=user)
    creds.set_api_key("test-key")
    creds.is_valid = True
    creds.save()

    today = timezone.localdate().isoformat()
    respx.get("https://intervals.icu/api/v1/athlete/0/activities").mock(
        return_value=Response(200, json=[{"id": "a1", "name": "Ride"}])
    )
    respx.get("https://intervals.icu/api/v1/activity/a1").mock(
        return_value=Response(
            200,
            json={
                "id": "a1",
                "name": "Ride",
                "type": "Ride",
                "start_date_local": f"{today}T10:00:00",
                "moving_time": 3600,
                "icu_training_load": 80,
            },
        )
    )
    respx.get("https://intervals.icu/api/v1/athlete/0/power-curves").mock(
        return_value=Response(
            200,
            json={
                "list": [
                    {
                        "secs": [300],
                        "values": [310],
                        "activity_id": ["a1"],
                    }
                ]
            },
        )
    )
    respx.get("https://intervals.icu/api/v1/athlete/0/hr-curves").mock(
        return_value=Response(200, json={"list": []})
    )

    activities = poll_new_activities(user)
    assert len(activities) == 1
    activity = Activity.objects.get(external_id="a1")
    assert activity.curve_prs == [
        {"metric": "power", "duration_sec": 300, "value": 310.0, "label": "5 мин"}
    ]
