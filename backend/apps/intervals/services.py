from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any

from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.intervals.client import IntervalsAPIError, IntervalsClient
from apps.intervals.models import Activity, AthleteSnapshot, CalendarEventCache, WellnessDay
from apps.users.models import TelegramUser

logger = logging.getLogger(__name__)


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = parse_datetime(str(value).replace("Z", "+00:00"))
        if parsed is None:
            try:
                parsed = datetime.fromisoformat(str(value))
            except ValueError:
                return None
    if timezone.is_naive(parsed):
        return timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def client_for_user(user: TelegramUser) -> IntervalsClient:
    creds = user.credentials
    return IntervalsClient(
        api_key=creds.get_api_key(),
        athlete_id=creds.athlete_id or "0",
    )


def extract_form_fields(wellness_row: dict) -> dict:
    """Map intervals.icu wellness fields to our snapshot.

    Form/TSB is often absent from the API and is computed as CTL − ATL.
    Subjective wellness ``fatigue`` (1–10) is intentionally last among fatigue keys.
    """
    fitness = _first(wellness_row, "icu_ctl", "ctl", "fitness")
    fatigue = _first(wellness_row, "icu_atl", "atl", "fatigue")
    form = _first(wellness_row, "icu_form", "form", "tsb", "icu_tsb")
    if form is None and fitness is not None and fatigue is not None:
        form = round(fitness - fatigue, 5)
    return {
        "fitness": fitness,
        "fatigue": fatigue,
        "form": form,
        "weight": _first(wellness_row, "weight", "icu_weight"),
        "vo2max": _first(wellness_row, "vo2max", "vo2_max", "icu_vo2max"),
    }


def _first(data: dict, *keys: str) -> float | None:
    for key in keys:
        if key in data and data[key] is not None:
            try:
                return float(data[key])
            except (TypeError, ValueError):
                continue
    return None


def _latest_non_null(rows: list[dict], *keys: str) -> float | None:
    """Walk newest→oldest and return the first non-null numeric field."""
    for row in rows:
        value = _first(row, *keys)
        if value is not None:
            return value
    return None


def _merge_form_fields(rows: list[dict], athlete: dict | None = None) -> dict:
    """Build snapshot from newest wellness row + last-known weight/VO2max.

    Daily wellness often has null weight/vo2max even when earlier days (or the
    athlete profile) still hold the latest measured values.
    """
    if not rows:
        fields = extract_form_fields(athlete or {})
    else:
        fields = extract_form_fields(rows[0])
        if fields.get("weight") is None:
            fields["weight"] = _latest_non_null(rows, "weight", "icu_weight")
        if fields.get("vo2max") is None:
            fields["vo2max"] = _latest_non_null(rows, "vo2max", "vo2_max", "icu_vo2max")

    if athlete:
        if fields.get("weight") is None:
            fields["weight"] = _first(athlete, "icu_weight", "weight")
        if fields.get("vo2max") is None:
            fields["vo2max"] = _first(athlete, "vo2max", "icu_vo2max")
        if fields.get("fitness") is None:
            fields["fitness"] = _first(athlete, "icu_ctl", "ctl")
        if fields.get("fatigue") is None:
            fields["fatigue"] = _first(athlete, "icu_atl", "atl")
        if (
            fields.get("form") is None
            and fields.get("fitness") is not None
            and fields.get("fatigue") is not None
        ):
            fields["form"] = round(fields["fitness"] - fields["fatigue"], 5)
    return fields


def extract_compliance(activity: dict) -> float | None:
    value = _first(
        activity,
        "icu_compliance",
        "compliance",
        "icu_intensity",
    )
    if value is None:
        return None
    # intensity is often 0-100-ish already; compliance sometimes 0-1
    if 0 <= value <= 1.5:
        return round(value * 100, 1)
    return round(value, 1)


def _as_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def extract_wellness_day_fields(row: dict) -> dict:
    """Map intervals.icu wellness row to WellnessDay fields."""
    form_fields = extract_form_fields(row)
    comments = row.get("comments") or row.get("note") or ""
    if comments is not None and not isinstance(comments, str):
        comments = str(comments)
    return {
        "sleep_secs": _as_int(_first(row, "sleepSecs", "sleep_secs")),
        "sleep_quality": _first(row, "sleepQuality", "sleep_quality"),
        "sleep_score": _first(row, "sleepScore", "sleep_score"),
        "resting_hr": _first(row, "restingHR", "resting_hr", "restingHr"),
        "avg_sleeping_hr": _first(row, "avgSleepingHR", "avg_sleeping_hr"),
        "hrv": _first(row, "hrv", "hrvSDNN"),
        "weight": form_fields.get("weight"),
        "fatigue": _first(row, "fatigue"),  # subjective wellness score
        "soreness": _first(row, "soreness"),
        "stress": _first(row, "stress"),
        "mood": _first(row, "mood"),
        "motivation": _first(row, "motivation"),
        "injury": _first(row, "injury"),
        "readiness": _first(row, "readiness"),
        "fitness": form_fields.get("fitness"),
        "fatigue_atl": _first(row, "icu_atl", "atl"),
        "form": form_fields.get("form"),
        "comments": comments or "",
    }


def _wellness_row_date(row: dict) -> date | None:
    raw = row.get("id") or row.get("date") or row.get("localDate")
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw)[:10])
    except ValueError:
        return None


def upsert_wellness_days(user: TelegramUser, rows: list[dict]) -> int:
    """Persist wellness rows for a user. Returns number of upserted days."""
    count = 0
    for row in rows:
        day = _wellness_row_date(row)
        if not day:
            continue
        fields = extract_wellness_day_fields(row)
        WellnessDay.objects.update_or_create(
            user=user,
            date=day,
            defaults={**fields, "raw_json": row},
        )
        count += 1
    return count


def sync_wellness_days(
    user: TelegramUser,
    *,
    oldest: date | None = None,
    newest: date | None = None,
) -> int:
    """Fetch wellness from ICU and upsert WellnessDay rows."""
    client = client_for_user(user)
    newest = newest or date.today()
    oldest = oldest or (newest - timedelta(days=90))
    rows = client.get_wellness(oldest.isoformat(), newest.isoformat())
    if not rows:
        return 0
    return upsert_wellness_days(user, rows)


@transaction.atomic
def sync_form(user: TelegramUser) -> AthleteSnapshot:
    client = client_for_user(user)
    newest = date.today()
    # Wider window so last measured weight / VO2max are still findable
    oldest = newest - timedelta(days=90)
    rows = client.get_wellness(oldest.isoformat(), newest.isoformat())
    if not rows:
        try:
            rows = client.get_fitness(oldest.isoformat(), newest.isoformat())
        except IntervalsAPIError:
            rows = []

    sorted_rows = sorted(
        rows,
        key=lambda r: str(r.get("id") or r.get("date") or ""),
        reverse=True,
    )
    if rows:
        upsert_wellness_days(user, rows)

    best = sorted_rows[0] if sorted_rows else {}
    athlete: dict = {}
    try:
        athlete = client.get_athlete() or {}
    except IntervalsAPIError:
        logger.warning("Could not fetch athlete profile for form fallback", exc_info=True)

    fields = _merge_form_fields(sorted_rows, athlete)
    as_of = None
    raw_id = best.get("id") or best.get("date")
    if raw_id:
        try:
            as_of = date.fromisoformat(str(raw_id)[:10])
        except ValueError:
            as_of = newest

    snapshot, _ = AthleteSnapshot.objects.update_or_create(
        user=user,
        defaults={
            **fields,
            "as_of_date": as_of or newest,
            "raw_json": best or {},
        },
    )
    return snapshot


@transaction.atomic
def sync_calendar(user: TelegramUser, past_days: int = 7, future_days: int = 14) -> int:
    client = client_for_user(user)
    today = date.today()
    oldest = (today - timedelta(days=past_days)).isoformat()
    newest = (today + timedelta(days=future_days)).isoformat()
    events = client.get_events(oldest, newest, resolve=True)
    seen_ids: set[str] = set()
    count = 0
    for event in events:
        external_id = str(event.get("id") or event.get("uid") or "")
        if not external_id:
            continue
        seen_ids.add(external_id)
        CalendarEventCache.objects.update_or_create(
            user=user,
            external_id=external_id,
            defaults={
                "category": event.get("category") or "",
                "name": event.get("name") or event.get("title") or "",
                "type": event.get("type") or "",
                "start_date_local": _parse_dt(event.get("start_date_local")),
                "end_date_local": _parse_dt(event.get("end_date_local")),
                "icu_training_load": _first(event, "icu_training_load"),
                "workout_doc": event.get("workout_doc") or {},
                "raw_json": event,
            },
        )
        count += 1

    # Drop stale events outside window that weren't returned
    CalendarEventCache.objects.filter(user=user).exclude(external_id__in=seen_ids).filter(
        start_date_local__date__gte=today - timedelta(days=past_days),
        start_date_local__date__lte=today + timedelta(days=future_days),
    ).delete()
    return count


def match_event_for_activity(user: TelegramUser, activity_data: dict) -> CalendarEventCache | None:
    start = _parse_dt(activity_data.get("start_date_local"))
    if not start:
        return None
    day = start.date()
    activity_type = (activity_data.get("type") or "").lower()
    qs = CalendarEventCache.objects.filter(
        user=user,
        start_date_local__date=day,
        category__iexact="WORKOUT",
    )
    if activity_type:
        typed = qs.filter(type__iexact=activity_data.get("type"))
        if typed.exists():
            qs = typed
    return qs.order_by("start_date_local").first()


def upsert_activity_from_detail(user: TelegramUser, detail: dict) -> Activity:
    external_id = str(detail.get("id") or "")
    matched = match_event_for_activity(user, detail)
    compliance = extract_compliance(detail)
    if compliance is None and matched and matched.icu_training_load and detail.get("icu_training_load"):
        planned = matched.icu_training_load
        actual = float(detail["icu_training_load"])
        if planned:
            compliance = round(min(actual / planned, 1.5) * 100, 1)

    intervals = detail.get("icu_intervals") or detail.get("intervals") or []
    from apps.intervals.zones import parse_hr_zone_secs, parse_power_zone_secs

    activity, _ = Activity.objects.update_or_create(
        user=user,
        external_id=external_id,
        defaults={
            "name": detail.get("name") or "",
            "type": detail.get("type") or "",
            "start_date_local": _parse_dt(detail.get("start_date_local")),
            "moving_time": detail.get("moving_time"),
            "distance": _first(detail, "distance"),
            "icu_training_load": _first(detail, "icu_training_load"),
            "icu_intensity": _first(detail, "icu_intensity"),
            "average_watts": _first(detail, "average_watts"),
            "weighted_average_watts": _first(detail, "weighted_average_watts", "icu_weighted_avg_watts"),
            "average_heartrate": _first(detail, "average_heartrate"),
            "max_heartrate": _first(detail, "max_heartrate"),
            "total_elevation_gain": _first(detail, "total_elevation_gain"),
            "compliance": compliance,
            "matched_event": matched,
            "intervals_json": intervals,
            "raw_json": detail,
            "power_zone_secs": parse_power_zone_secs(detail),
            "hr_zone_secs": parse_hr_zone_secs(detail),
        },
    )
    return activity


def sync_activities_for_range(user: TelegramUser, lookback_days: int = 7) -> list[Activity]:
    """Fetch and upsert all activities in the lookback window (refresh zone fields)."""
    from apps.intervals.curves import refresh_activity_curve_prs

    client = client_for_user(user)
    newest = date.today()
    oldest = newest - timedelta(days=lookback_days)
    summaries = client.get_activities(oldest.isoformat(), newest.isoformat())
    updated: list[Activity] = []
    for summary in summaries:
        external_id = str(summary.get("id") or "")
        if not external_id:
            continue
        detail = client.get_activity(external_id, intervals=True)
        activity = upsert_activity_from_detail(user, detail)
        try:
            refresh_activity_curve_prs(client, activity)
        except Exception:
            logger.exception(
                "curve PR detection failed for activity %s", activity.external_id
            )
        updated.append(activity)
    return updated


def poll_new_activities(user: TelegramUser, lookback_days: int = 3) -> list[Activity]:
    from apps.intervals.curves import refresh_activity_curve_prs

    client = client_for_user(user)
    newest = date.today()
    oldest = newest - timedelta(days=lookback_days)
    summaries = client.get_activities(oldest.isoformat(), newest.isoformat())
    new_or_updated: list[Activity] = []
    for summary in summaries:
        external_id = str(summary.get("id") or "")
        if not external_id:
            continue
        existing = Activity.objects.filter(user=user, external_id=external_id).first()
        if existing and existing.report_sent_at:
            continue
        detail = client.get_activity(external_id, intervals=True)
        activity = upsert_activity_from_detail(user, detail)
        try:
            refresh_activity_curve_prs(client, activity)
        except Exception:
            logger.exception(
                "curve PR detection failed for activity %s", activity.external_id
            )
        new_or_updated.append(activity)
    return new_or_updated


def validate_credentials(user: TelegramUser) -> bool:
    creds = user.credentials
    client = client_for_user(user)
    try:
        athlete = client.get_athlete()
        creds.is_valid = True
        creds.last_validated_at = timezone.now()
        creds.last_error = ""
        if athlete.get("id"):
            creds.athlete_id = str(athlete["id"])
        creds.save()
        return True
    except IntervalsAPIError as exc:
        creds.is_valid = False
        creds.last_error = str(exc)
        creds.last_validated_at = timezone.now()
        creds.save(update_fields=["is_valid", "last_error", "last_validated_at", "updated_at"])
        return False


def extract_athlete_ftp(athlete: dict | None) -> float | None:
    if not athlete:
        return None
    value = _first(
        athlete,
        "icu_ftp",
        "ftp",
        "icu_power_ftp",
        "threshold_power",
    )
    if value is None:
        sport_settings = athlete.get("sportSettings") or athlete.get("sport_settings") or []
        if isinstance(sport_settings, list):
            for row in sport_settings:
                if not isinstance(row, dict):
                    continue
                value = _first(row, "ftp", "icu_ftp", "threshold_power")
                if value is not None:
                    break
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _render_plan_event_chart(ev: CalendarEventCache, ftp: float | None) -> str | None:
    from apps.charts.renderer import render_intervals_chart, steps_to_intervals

    doc = ev.workout_doc or {}
    intervals = steps_to_intervals(doc, ftp=ftp)
    if not intervals:
        return None
    return render_intervals_chart(
        f"plan_{ev.external_id}",
        intervals,
        title=ev.name or "План",
    )


def plan_payload(user: TelegramUser, with_charts: bool = True) -> dict:
    from apps.notifications.services import format_plan_event

    tz = user.timezone
    now_local = timezone.now().astimezone(tz)
    today = now_local.date()
    tomorrow = today + timedelta(days=1)

    events = list(
        CalendarEventCache.objects.filter(user=user)
        .filter(start_date_local__date__gte=today)
        .order_by("start_date_local")[:20]
    )

    ftp = None
    if with_charts:
        try:
            athlete = client_for_user(user).get_athlete() or {}
            ftp = extract_athlete_ftp(athlete)
        except Exception:
            logger.warning("Could not fetch athlete FTP for plan charts", exc_info=True)

    def serialize(ev: CalendarEventCache) -> dict:
        chart_path = None
        if with_charts and (ev.category or "").upper() == "WORKOUT":
            try:
                chart_path = _render_plan_event_chart(ev, ftp)
            except Exception:
                logger.exception("Failed to render plan chart for %s", ev.external_id)
        payload = {
            "id": ev.external_id,
            "name": ev.name,
            "type": ev.type,
            "category": ev.category,
            "start_date_local": ev.start_date_local.isoformat() if ev.start_date_local else None,
            "icu_training_load": ev.icu_training_load,
            "workout_doc": ev.workout_doc,
            "caption": format_plan_event(ev),
            "chart_path": chart_path,
        }
        return payload

    today_events = [serialize(e) for e in events if e.start_date_local and e.start_date_local.date() == today]
    tomorrow_events = [
        serialize(e) for e in events if e.start_date_local and e.start_date_local.date() == tomorrow
    ]
    upcoming = [serialize(e) for e in events[:5]]

    # Unique workout messages: today + tomorrow + upcoming not already included
    seen_ids: set[str] = set()
    workout_messages: list[dict] = []
    for bucket in (today_events, tomorrow_events, upcoming):
        for item in bucket:
            eid = str(item.get("id") or "")
            if not eid or eid in seen_ids:
                continue
            seen_ids.add(eid)
            if item.get("chart_path") or (item.get("category") or "").upper() == "WORKOUT":
                workout_messages.append(
                    {
                        "id": item["id"],
                        "caption": item["caption"],
                        "chart_path": item.get("chart_path"),
                        "name": item.get("name"),
                        "type": item.get("type"),
                        "start_date_local": item.get("start_date_local"),
                    }
                )

    return {
        "today": today_events,
        "tomorrow": tomorrow_events,
        "upcoming": upcoming,
        "workouts": workout_messages,
        "as_of": timezone.now().isoformat(),
    }


def recent_reports_payload(user: TelegramUser, limit: int = 5) -> dict:
    from pathlib import Path

    from django.conf import settings

    from apps.charts.renderer import render_intervals_chart
    from apps.notifications.services import format_activity_report

    limit = max(1, min(int(limit or 5), 10))
    activities = list(
        Activity.objects.filter(user=user)
        .select_related("matched_event")
        .order_by("-start_date_local")[:limit]
    )
    media_root = Path(settings.MEDIA_ROOT)
    items: list[dict] = []
    for activity in activities:
        chart_path = activity.chart_path
        intervals = activity.intervals_json or []
        needs_render = not chart_path or not (media_root / chart_path).exists()
        if needs_render:
            try:
                chart_path = render_intervals_chart(
                    activity.external_id,
                    intervals,
                    title=activity.name or "Intervals",
                )
                activity.chart_path = chart_path
                activity.save(update_fields=["chart_path"])
            except Exception:
                logger.exception("Failed to render chart for activity %s", activity.external_id)
                chart_path = None
        items.append(
            {
                "id": activity.external_id,
                "name": activity.name,
                "type": activity.type,
                "start_date_local": (
                    activity.start_date_local.isoformat() if activity.start_date_local else None
                ),
                "caption": format_activity_report(activity),
                "ai_summary": activity.ai_summary or "",
                "chart_path": chart_path,
            }
        )
    return {"items": items, "as_of": timezone.now().isoformat()}


def form_payload(user: TelegramUser, with_chart: bool = False) -> dict:
    from apps.charts.renderer import render_form_trend_chart

    snapshot = getattr(user, "athlete_snapshot", None)
    if not snapshot:
        return {"connected": True, "data": None}

    payload: dict = {
        "connected": True,
        "data": {
            "fitness": snapshot.fitness,
            "fatigue": snapshot.fatigue,
            "form": snapshot.form,
            "weight": snapshot.weight,
            "vo2max": snapshot.vo2max,
            "as_of_date": snapshot.as_of_date.isoformat() if snapshot.as_of_date else None,
            "synced_at": snapshot.synced_at.isoformat() if snapshot.synced_at else None,
        },
    }

    if with_chart:
        end = date.today()
        start = end - timedelta(days=29)
        days = list(
            WellnessDay.objects.filter(user=user, date__gte=start, date__lte=end).order_by(
                "date"
            )
        )
        series = [
            {
                "date": d.date.isoformat(),
                "ctl": d.fitness,
                "atl": d.fatigue_atl,
                "tsb": d.form,
            }
            for d in days
            if d.fitness is not None or d.fatigue_atl is not None or d.form is not None
        ]
        chart_path = ""
        caption = "📈 CTL / ATL / TSB — последние 30 дней"
        if series:
            try:
                chart_path = render_form_trend_chart(
                    user.id,
                    series,
                    title="Форма: CTL / ATL / TSB (30 дней)",
                    end_date=end,
                )
            except Exception:
                logger.exception("form trend chart failed user=%s", user.id)
        payload["chart_path"] = chart_path
        payload["chart_caption"] = caption

    return payload


def zones_payload(
    user: TelegramUser,
    period_start: date | None = None,
    period_end: date | None = None,
) -> dict:
    from apps.charts.renderer import render_zone_distribution_chart
    from apps.intervals.zones import (
        aggregate_actual_zones,
        aggregate_planned_zones,
        current_week_bounds,
        format_zone_caption,
    )

    if period_start is None or period_end is None:
        period_start, period_end = current_week_bounds(user)

    actual = aggregate_actual_zones(user, period_start, period_end)
    planned = aggregate_planned_zones(
        user, period_start, period_end, source=actual.get("source") or "power"
    )
    caption = format_zone_caption(actual, planned, period_start, period_end)
    chart_path = ""
    try:
        chart_path = render_zone_distribution_chart(
            user_id=user.id,
            actual_secs=actual["secs"],
            planned_secs=planned["secs"] if planned.get("has_plan") else None,
            source=actual.get("source") or "power",
            period_start=period_start,
            period_end=period_end,
            title=f"Зоны {period_start.isoformat()} — {period_end.isoformat()}",
        )
    except Exception:
        logger.exception("zone distribution chart failed user=%s", user.id)

    return {
        "connected": True,
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "actual": actual,
        "planned": planned,
        "source": actual.get("source"),
        "chart_path": chart_path,
        "caption": caption,
    }
