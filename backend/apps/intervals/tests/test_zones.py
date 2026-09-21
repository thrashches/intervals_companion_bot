from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from django.utils import timezone

from apps.charts.renderer import render_form_trend_chart, render_zone_distribution_chart
from apps.intervals.models import Activity, CalendarEventCache, WellnessDay
from apps.intervals.services import form_payload, upsert_activity_from_detail, zones_payload
from apps.intervals.zones import (
    aggregate_actual_zones,
    aggregate_planned_zones,
    collapse_to_z5,
    parse_hr_zone_secs,
    parse_power_zone_secs,
)
from apps.users.models import TelegramUser


def test_parse_power_zone_secs():
    detail = {
        "icu_zone_times": [
            {"id": "Z1", "secs": 600},
            {"secs": 1200},
            300,
            None,
            {"secs": 180},
            {"secs": 60},
            {"secs": 30},
        ]
    }
    assert parse_power_zone_secs(detail) == [600, 1200, 300, 0, 180, 60, 30]


def test_parse_hr_zone_secs():
    assert parse_hr_zone_secs({"icu_hr_zone_times": [100, 200, 50]}) == [100, 200, 50]
    assert parse_hr_zone_secs({}) == []


def test_collapse_to_z5():
    assert collapse_to_z5([10, 20, 30, 40, 50, 60, 70]) == [10, 20, 30, 40, 180]
    assert collapse_to_z5([1, 2]) == [1, 2, 0, 0, 0]
    assert collapse_to_z5(None) == [0, 0, 0, 0, 0]


@pytest.mark.django_db
def test_upsert_stores_zone_secs():
    user = TelegramUser.objects.create(telegram_id=9001, first_name="T")
    detail = {
        "id": "act1",
        "name": "Ride",
        "type": "Ride",
        "start_date_local": timezone.now().isoformat(),
        "moving_time": 3600,
        "icu_zone_times": [{"secs": 100}, {"secs": 200}, {"secs": 50}],
        "icu_hr_zone_times": [10, 20, 30, 40, 50],
        "icu_intervals": [],
    }
    activity = upsert_activity_from_detail(user, detail)
    assert activity.power_zone_secs == [100, 200, 50]
    assert activity.hr_zone_secs == [10, 20, 30, 40, 50]


@pytest.mark.django_db
def test_aggregate_actual_and_planned_zones():
    user = TelegramUser.objects.create(telegram_id=9002, first_name="T", timezone="UTC")
    today = date.today()
    week_start = today - timedelta(days=today.weekday())
    start_dt = timezone.make_aware(datetime.combine(week_start, datetime.min.time()))

    Activity.objects.create(
        user=user,
        external_id="a1",
        name="Ride",
        type="Ride",
        start_date_local=start_dt,
        power_zone_secs=[600, 1200, 0, 300, 100, 50],
        hr_zone_secs=[1, 2, 3],
    )
    CalendarEventCache.objects.create(
        user=user,
        external_id="e1",
        category="WORKOUT",
        name="Plan",
        start_date_local=start_dt,
        workout_doc={"zoneTimes": [500, 1000, 0, 200, 0, 0, 0]},
    )

    actual = aggregate_actual_zones(user, week_start, week_start + timedelta(days=6))
    assert actual["source"] == "power"
    assert actual["secs"] == [600, 1200, 0, 300, 150]

    planned = aggregate_planned_zones(
        user, week_start, week_start + timedelta(days=6), source="power"
    )
    assert planned["has_plan"] is True
    assert planned["secs"] == [500, 1000, 0, 200, 0]


@pytest.mark.django_db
def test_aggregate_falls_back_to_hr():
    user = TelegramUser.objects.create(telegram_id=9003, first_name="T", timezone="UTC")
    today = date.today()
    start_dt = timezone.make_aware(datetime.combine(today, datetime.min.time()))
    Activity.objects.create(
        user=user,
        external_id="a2",
        name="Run",
        start_date_local=start_dt,
        hr_zone_secs=[100, 200, 300, 0, 0],
    )
    actual = aggregate_actual_zones(user, today, today)
    assert actual["source"] == "hr"
    assert actual["secs"] == [100, 200, 300, 0, 0]


def test_render_form_trend_and_zones_smoke(tmp_path, settings):
    settings.MEDIA_ROOT = str(tmp_path)
    series = [
        {"date": "2026-09-01", "ctl": 50, "atl": 40, "tsb": 10},
        {"date": "2026-09-02", "ctl": 51, "atl": 42, "tsb": 9},
        {"date": "2026-09-03", "ctl": 52, "atl": 45, "tsb": 7},
    ]
    form_path = render_form_trend_chart(1, series, title="Test form", end_date=date(2026, 9, 3))
    assert form_path.startswith("charts/")
    assert (Path(settings.MEDIA_ROOT) / form_path).is_file()

    zones_path = render_zone_distribution_chart(
        user_id=1,
        actual_secs=[600, 1200, 300, 100, 50],
        planned_secs=[500, 1000, 200, 150, 0],
        source="power",
        period_start=date(2026, 9, 1),
        period_end=date(2026, 9, 7),
        title="Test zones",
    )
    assert zones_path.startswith("charts/")
    assert (Path(settings.MEDIA_ROOT) / zones_path).is_file()


@pytest.mark.django_db
def test_form_payload_with_chart(tmp_path, settings):
    settings.MEDIA_ROOT = str(tmp_path)
    user = TelegramUser.objects.create(telegram_id=9004, first_name="T")
    from apps.intervals.models import AthleteSnapshot

    AthleteSnapshot.objects.create(
        user=user, fitness=60, fatigue=50, form=10, as_of_date=date.today()
    )
    for i in range(5):
        WellnessDay.objects.create(
            user=user,
            date=date.today() - timedelta(days=i),
            fitness=60 + i,
            fatigue_atl=50 + i,
            form=10 - i,
        )
    payload = form_payload(user, with_chart=True)
    assert payload["data"]["fitness"] == 60
    assert payload["chart_path"]
    assert (Path(settings.MEDIA_ROOT) / payload["chart_path"]).is_file()


@pytest.mark.django_db
def test_zones_payload_returns_chart(tmp_path, settings):
    settings.MEDIA_ROOT = str(tmp_path)
    user = TelegramUser.objects.create(telegram_id=9005, first_name="T", timezone="UTC")
    today = date.today()
    week_start = today - timedelta(days=today.weekday())
    start_dt = timezone.make_aware(datetime.combine(week_start, datetime.min.time()))
    Activity.objects.create(
        user=user,
        external_id="z1",
        start_date_local=start_dt,
        power_zone_secs=[100, 200, 50, 0, 0],
    )
    payload = zones_payload(user, period_start=week_start, period_end=week_start + timedelta(days=6))
    assert payload["chart_path"]
    assert payload["actual"]["secs"] == [100, 200, 50, 0, 0]
    assert "План" in payload["caption"] or "план" in payload["caption"].lower()
    assert (Path(settings.MEDIA_ROOT) / payload["chart_path"]).is_file()
