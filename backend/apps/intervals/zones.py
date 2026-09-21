from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from django.utils import timezone

from apps.intervals.models import Activity, CalendarEventCache
from apps.users.models import TelegramUser

ZoneSource = Literal["power", "hr"]


def _as_int_secs(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, dict):
        value = value.get("secs")
    try:
        return max(int(float(value)), 0)
    except (TypeError, ValueError):
        return 0


def parse_power_zone_secs(detail: dict | None) -> list[int]:
    """Extract seconds per power zone from activity detail / raw_json."""
    if not detail:
        return []
    raw = detail.get("icu_zone_times")
    if not isinstance(raw, list):
        return []
    return [_as_int_secs(item) for item in raw]


def parse_hr_zone_secs(detail: dict | None) -> list[int]:
    """Extract seconds per HR zone from activity detail / raw_json."""
    if not detail:
        return []
    raw = detail.get("icu_hr_zone_times")
    if not isinstance(raw, list):
        return []
    return [_as_int_secs(item) for item in raw]


def collapse_to_z5(secs: list[int] | None) -> list[int]:
    """
    Collapse zone seconds into 5 buckets: Z1–Z4 and Z5+ (Z5 and above).
    Missing entries are treated as 0.
    """
    values = [int(v or 0) for v in (secs or [])]
    buckets = [0, 0, 0, 0, 0]
    for idx, value in enumerate(values):
        if idx < 4:
            buckets[idx] += value
        else:
            buckets[4] += value
    return buckets


def _sum_zone_lists(rows: list[list[int]]) -> list[int]:
    if not rows:
        return []
    width = max(len(row) for row in rows)
    total = [0] * width
    for row in rows:
        for idx in range(width):
            total[idx] += int(row[idx]) if idx < len(row) else 0
    return total


def _activity_power_secs(activity: Activity) -> list[int]:
    if activity.power_zone_secs:
        return [int(v or 0) for v in activity.power_zone_secs]
    return parse_power_zone_secs(activity.raw_json or {})


def _activity_hr_secs(activity: Activity) -> list[int]:
    if activity.hr_zone_secs:
        return [int(v or 0) for v in activity.hr_zone_secs]
    return parse_hr_zone_secs(activity.raw_json or {})


def _user_tz(user: TelegramUser) -> ZoneInfo:
    try:
        return ZoneInfo(str(user.timezone))
    except Exception:
        return ZoneInfo("Europe/Moscow")


def _day_bounds(user: TelegramUser, start: date, end: date) -> tuple[datetime, datetime]:
    """Inclusive local-date range as aware UTC-comparable datetimes."""
    tz = _user_tz(user)
    start_local = datetime.combine(start, time.min, tzinfo=tz)
    end_local = datetime.combine(end + timedelta(days=1), time.min, tzinfo=tz)
    return start_local, end_local


def _activities_in_range(user: TelegramUser, start: date, end: date):
    start_dt, end_dt = _day_bounds(user, start, end)
    return Activity.objects.filter(
        user=user,
        start_date_local__gte=start_dt,
        start_date_local__lt=end_dt,
    )


def aggregate_actual_zones(
    user: TelegramUser, start: date, end: date
) -> dict[str, Any]:
    """
    Sum actual zone seconds for activities in [start, end] (inclusive local dates).
    Prefer power zones if any activity has them; otherwise HR.
    """
    activities = list(_activities_in_range(user, start, end))
    power_rows = [_activity_power_secs(a) for a in activities]
    power_rows = [r for r in power_rows if any(r)]
    if power_rows:
        secs = collapse_to_z5(_sum_zone_lists(power_rows))
        return {"secs": secs, "source": "power", "activity_count": len(activities)}

    hr_rows = [_activity_hr_secs(a) for a in activities]
    hr_rows = [r for r in hr_rows if any(r)]
    secs = collapse_to_z5(_sum_zone_lists(hr_rows)) if hr_rows else [0, 0, 0, 0, 0]
    return {
        "secs": secs,
        "source": "hr" if hr_rows else "power",
        "activity_count": len(activities),
    }


def _planned_zone_list(doc: dict, source: ZoneSource) -> list[int]:
    if source == "power":
        raw = doc.get("zoneTimes") or doc.get("zone_times")
    else:
        raw = doc.get("hrZoneTimes") or doc.get("hr_zone_times")
    if not isinstance(raw, list):
        return []
    return [_as_int_secs(item) for item in raw]


def aggregate_planned_zones(
    user: TelegramUser,
    start: date,
    end: date,
    source: ZoneSource = "power",
) -> dict[str, Any]:
    """Sum planned zone times from WORKOUT calendar events in the date range."""
    start_dt, end_dt = _day_bounds(user, start, end)
    events = CalendarEventCache.objects.filter(
        user=user,
        category__iexact="WORKOUT",
        start_date_local__gte=start_dt,
        start_date_local__lt=end_dt,
    )
    rows: list[list[int]] = []
    for ev in events:
        row = _planned_zone_list(ev.workout_doc or {}, source)
        if any(row):
            rows.append(row)
    secs = collapse_to_z5(_sum_zone_lists(rows)) if rows else [0, 0, 0, 0, 0]
    return {
        "secs": secs,
        "source": source,
        "event_count": events.count(),
        "has_plan": bool(rows),
    }


def current_week_bounds(user: TelegramUser, today: date | None = None) -> tuple[date, date]:
    """Monday–Sunday week for the user's local today."""
    if today is None:
        today = timezone.now().astimezone(_user_tz(user)).date()
    week_start = today - timedelta(days=today.weekday())
    week_end = week_start + timedelta(days=6)
    return week_start, week_end


def format_zone_caption(
    actual: dict[str, Any],
    planned: dict[str, Any],
    period_start: date,
    period_end: date,
) -> str:
    source_label = "мощность" if actual.get("source") == "power" else "пульс"
    total = sum(actual.get("secs") or [])
    plan_total = sum(planned.get("secs") or [])
    lines = [
        f"📶 Зоны {period_start.isoformat()} — {period_end.isoformat()} ({source_label})",
    ]
    if total:
        lines.append(f"Факт: {_fmt_duration(total)}")
    else:
        lines.append("Факт: нет данных по зонам")
    if planned.get("has_plan") and plan_total:
        lines.append(f"План: {_fmt_duration(plan_total)}")
    else:
        lines.append("План на неделю не найден")
    return "\n".join(lines)


def _fmt_duration(secs: int) -> str:
    hours, rem = divmod(int(secs), 3600)
    minutes = rem // 60
    if hours:
        return f"{hours}ч {minutes}м"
    return f"{minutes}м"
