from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from typing import Any

from django.conf import settings

from apps.ai.client import DeepSeekClient, DeepSeekError
from apps.intervals.models import Activity, CalendarEventCache, WellnessDay
from apps.users.models import TelegramUser

logger = logging.getLogger(__name__)

_PROMPT_SHARED_RULES = """
- Пиши обычным текстом без HTML и markdown-таблиц.
- Не ставь медицинские диагнозы.
- Если данных мало — честно отметь неопределённость, не выдумывай цифры.
- Начинай каждый блок с заголовка вида «1. …», «2. …», «3. …», «4. …».
- Compliance (% соответствия плану) часто считается некорректно: нет матча с планом, другой тип активности, ERG/станок vs улица, fallback по load. Не делай compliance главным критерием «выполнил/не выполнил». Опирайся на load, intensity, время, мощность/ЧСС и сравнение с планом; при сомнении явно пиши, что % соответствия ненадёжен.
- Учитывай environment (indoor/outdoor) и trainer: на станке иначе оценивай мощность (ERG, indoor FTP); на улице — рельеф, ветер, стопы. Не сравнивай outdoor и indoor «в лоб» по одним и тем же ваттам без оговорки.
- Учитывай, что данные о здоровье (качество сна, мотивация, самочувствие) идут по шкале от 1 до 5 или от 1 до 10. Где 1 - хорошо, 5 или 10 - плохо.
- В блоке здоровья опирайся на wellness: HRV, resting HR, сон (sleep_hours / quality / score), вес и тренды (hrv_trend, resting_hr_trend, sleep_trend, weight_trend). Не подменяй восстановление средним пульсом тренировки.
- В блоке нагрузки/формы обязательно используй Fitness (CTL), Fatigue (ATL) и Form/TSB (обычно CTL−ATL) из form / form_trend / wellness. Form > 0 — относительно свеж; сильно отрицательный Form — накопленная усталость. Не своди оценку нагрузки к среднему HR.
- Если в контексте есть personal_records или curve_prs у активностей — обязательно отметь личные рекорды (например новую лучшую 5-минутку) и поздравь; не ограничивайся фразой «хорошая тренировка».
"""

SYSTEM_PROMPT_DAY = f"""Ты — тренер-аналитик endurance-спорта (вело/бег/триатлон).
По данным одного дня дай краткую оценку на русском языке строго в четырёх блоках:

1. План и факт
2. Нагрузка и восстановление
3. Здоровье (сон, пульс, вес, самочувствие)
4. Выводы на завтра

Правила:
- Объём ответа 900–1400 символов.
- Учитывай план, все тренировки дня и wellness-данные.
{_PROMPT_SHARED_RULES.strip()}
"""

SYSTEM_PROMPT_WEEK = f"""Ты — тренер-аналитик endurance-спорта (вело/бег/триатлон).
По данным недели дай краткую оценку на русском языке строго в четырёх блоках:

1. Выполнение плана за неделю
2. Нагрузка, форма и усталость
3. Здоровье и восстановление (сон, пульс, вес, болезнь/травма)
4. Выводы на следующую неделю

Правила:
- Объём ответа 1100–1600 символов.
- Учитывай план, все тренировки и wellness за период.
{_PROMPT_SHARED_RULES.strip()}
"""


def activity_environment(activity: Activity) -> tuple[str | None, bool | None]:
    """Return (environment, trainer) from activity type and ICU raw_json."""
    raw = activity.raw_json if isinstance(activity.raw_json, dict) else {}
    trainer_raw = raw.get("trainer")
    if isinstance(trainer_raw, bool):
        trainer: bool | None = trainer_raw
    elif trainer_raw is None:
        trainer = None
    else:
        trainer = bool(trainer_raw)

    type_name = (activity.type or "").strip()
    is_virtual = "virtual" in type_name.lower()
    if trainer is True or is_virtual:
        return "indoor", trainer
    if type_name:
        return "outdoor", trainer
    return None, trainer


def _compact_workout_doc(workout_doc: dict | None) -> Any:
    if not workout_doc:
        return None
    steps = workout_doc.get("steps") if isinstance(workout_doc, dict) else None
    if isinstance(steps, list) and steps:
        compact_steps = []
        for step in steps[:10]:
            if not isinstance(step, dict):
                continue
            compact_steps.append(
                {
                    "name": step.get("name") or step.get("description"),
                    "duration": step.get("duration") or step.get("length"),
                    "power": step.get("power"),
                    "hr": step.get("hr") or step.get("heartrate"),
                    "reps": step.get("reps") or step.get("repeat"),
                }
            )
        return {"steps": compact_steps}
    try:
        raw = json.dumps(workout_doc, ensure_ascii=False)
    except (TypeError, ValueError):
        return None
    return raw[:500]


def compact_wellness_day(day: WellnessDay) -> dict[str, Any]:
    sleep_hours = None
    if day.sleep_secs is not None:
        sleep_hours = round(day.sleep_secs / 3600, 2)
    return {
        "date": day.date.isoformat(),
        "sleep_hours": sleep_hours,
        "sleep_quality": day.sleep_quality,
        "sleep_score": day.sleep_score,
        "resting_hr": day.resting_hr,
        "avg_sleeping_hr": day.avg_sleeping_hr,
        "hrv": day.hrv,
        "weight": day.weight,
        "fatigue_subjective": day.fatigue,
        "soreness": day.soreness,
        "stress": day.stress,
        "mood": day.mood,
        "motivation": day.motivation,
        "injury": day.injury,
        "readiness": day.readiness,
        "fitness_ctl": day.fitness,
        "fatigue_atl": day.fatigue_atl,
        "form_tsb": day.form,
        "comments": (day.comments or "")[:200] or None,
    }


def _avg(values: list[float]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 2)


def _wellness_trend(
    days: list[WellnessDay],
    *,
    field: str,
    value_key: str,
    transform=None,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for day in days:
        value = getattr(day, field, None)
        if value is None:
            continue
        if transform is not None:
            value = transform(value)
        out.append({"date": day.date.isoformat(), value_key: value})
    return out


def _form_trend_from_wellness(
    wellness: list[WellnessDay],
) -> dict[str, Any] | None:
    """First/last CTL/ATL/TSB within period plus deltas."""
    with_form = [
        w
        for w in wellness
        if w.fitness is not None or w.fatigue_atl is not None or w.form is not None
    ]
    if not with_form:
        return None
    first, last = with_form[0], with_form[-1]

    def _delta(a: float | None, b: float | None) -> float | None:
        if a is None or b is None:
            return None
        return round(b - a, 2)

    return {
        "start_date": first.date.isoformat(),
        "end_date": last.date.isoformat(),
        "start": {
            "fitness_ctl": first.fitness,
            "fatigue_atl": first.fatigue_atl,
            "form_tsb": first.form,
        },
        "end": {
            "fitness_ctl": last.fitness,
            "fatigue_atl": last.fatigue_atl,
            "form_tsb": last.form,
        },
        "delta": {
            "fitness_ctl": _delta(first.fitness, last.fitness),
            "fatigue_atl": _delta(first.fatigue_atl, last.fatigue_atl),
            "form_tsb": _delta(first.form, last.form),
        },
    }


def _compact_activity(activity: Activity) -> dict[str, Any]:
    raw = activity.raw_json if isinstance(activity.raw_json, dict) else {}
    environment, trainer = activity_environment(activity)
    device_watts = raw.get("device_watts")
    if device_watts is not None and not isinstance(device_watts, bool):
        device_watts = bool(device_watts)

    curve_prs = activity.curve_prs if isinstance(activity.curve_prs, list) else []
    payload: dict[str, Any] = {
        "name": activity.name,
        "type": activity.type,
        "start_date_local": (
            activity.start_date_local.isoformat() if activity.start_date_local else None
        ),
        "moving_time": activity.moving_time,
        "distance_m": activity.distance,
        "icu_training_load": activity.icu_training_load,
        "icu_intensity": activity.icu_intensity,
        "compliance": activity.compliance,
        "compliance_note": "may_be_unreliable",
        "environment": environment,
        "trainer": trainer,
        "average_watts": activity.average_watts,
        "weighted_average_watts": activity.weighted_average_watts,
        "average_heartrate": activity.average_heartrate,
        "max_heartrate": activity.max_heartrate,
        "total_elevation_gain": activity.total_elevation_gain,
        "matched_plan": activity.matched_event.name if activity.matched_event_id else None,
    }
    if device_watts is not None:
        payload["device_watts"] = device_watts
    if curve_prs:
        payload["curve_prs"] = curve_prs
    return payload


def _compact_plan_event(event: CalendarEventCache) -> dict[str, Any]:
    return {
        "name": event.name,
        "type": event.type,
        "category": event.category,
        "start_date_local": (
            event.start_date_local.isoformat() if event.start_date_local else None
        ),
        "icu_training_load": event.icu_training_load,
        "workout_doc": _compact_workout_doc(event.workout_doc),
    }


def resolve_period_dates(
    kind: str,
    *,
    period_start: date | None = None,
    period_end: date | None = None,
    today: date | None = None,
) -> tuple[date, date]:
    """Return (start, end) inclusive for day or week analysis."""
    today = today or date.today()
    if kind == "day":
        start = period_start or today
        end = period_end or start
        return start, end
    if kind == "week":
        if period_start and period_end:
            return period_start, period_end
        # ISO week Mon–Sun containing `today` (or period_start)
        anchor = period_start or today
        start = anchor - timedelta(days=anchor.weekday())
        end = start + timedelta(days=6)
        return start, end
    raise ValueError(f"Unknown analysis kind: {kind}")


def build_period_context(
    user: TelegramUser,
    *,
    period_start: date,
    period_end: date,
) -> dict[str, Any]:
    snapshot = getattr(user, "athlete_snapshot", None)

    activities = list(
        Activity.objects.filter(
            user=user,
            start_date_local__date__gte=period_start,
            start_date_local__date__lte=period_end,
        )
        .select_related("matched_event")
        .order_by("start_date_local")
    )
    plan_events = list(
        CalendarEventCache.objects.filter(
            user=user,
            start_date_local__date__gte=period_start,
            start_date_local__date__lte=period_end,
            category__iexact="WORKOUT",
        ).order_by("start_date_local")
    )
    # Also include RACE in plan context
    race_events = list(
        CalendarEventCache.objects.filter(
            user=user,
            start_date_local__date__gte=period_start,
            start_date_local__date__lte=period_end,
            category__iexact="RACE",
        ).order_by("start_date_local")
    )
    wellness = list(
        WellnessDay.objects.filter(
            user=user,
            date__gte=period_start,
            date__lte=period_end,
        ).order_by("date")
    )

    lookback_start = period_start - timedelta(days=14)
    lookback_days = list(
        WellnessDay.objects.filter(
            user=user,
            date__gte=lookback_start,
            date__lte=period_end,
        ).order_by("date")
    )

    weight_trend = _wellness_trend(lookback_days, field="weight", value_key="weight")
    hrv_trend = _wellness_trend(lookback_days, field="hrv", value_key="hrv")
    resting_hr_trend = _wellness_trend(
        lookback_days, field="resting_hr", value_key="resting_hr"
    )
    sleep_trend = _wellness_trend(
        lookback_days,
        field="sleep_secs",
        value_key="sleep_hours",
        transform=lambda secs: round(secs / 3600, 2),
    )

    sleep_hours_vals = [
        round(w.sleep_secs / 3600, 2) for w in wellness if w.sleep_secs is not None
    ]
    resting_hr_vals = [w.resting_hr for w in wellness if w.resting_hr is not None]
    hrv_vals = [w.hrv for w in wellness if w.hrv is not None]

    personal_records: list[dict[str, Any]] = []
    for activity in activities:
        prs = activity.curve_prs if isinstance(activity.curve_prs, list) else []
        for pr in prs:
            if not isinstance(pr, dict):
                continue
            personal_records.append(
                {
                    "activity_name": activity.name,
                    "activity_id": activity.external_id,
                    **pr,
                }
            )

    context: dict[str, Any] = {
        "period": {
            "start": period_start.isoformat(),
            "end": period_end.isoformat(),
        },
        "plan": [_compact_plan_event(e) for e in plan_events + race_events],
        "activities": [_compact_activity(a) for a in activities],
        "wellness": [compact_wellness_day(w) for w in wellness],
        "weight_trend": weight_trend,
        "hrv_trend": hrv_trend,
        "resting_hr_trend": resting_hr_trend,
        "sleep_trend": sleep_trend,
        "form": None,
        "form_trend": _form_trend_from_wellness(wellness),
        "personal_records": personal_records,
        "totals": {
            "planned_workouts": len(plan_events),
            "completed_activities": len(activities),
            "total_load": sum((a.icu_training_load or 0) for a in activities),
            "planned_load": sum((e.icu_training_load or 0) for e in plan_events),
            "total_moving_time_sec": sum((a.moving_time or 0) for a in activities),
            "avg_sleep_hours": _avg(sleep_hours_vals),
            "avg_resting_hr": _avg(resting_hr_vals),
            "avg_hrv": _avg(hrv_vals),
        },
    }

    if snapshot:
        context["form"] = {
            "fitness_ctl": snapshot.fitness,
            "fatigue_atl": snapshot.fatigue,
            "form_tsb": snapshot.form,
            "weight": snapshot.weight,
            "vo2max": snapshot.vo2max,
            "as_of_date": snapshot.as_of_date.isoformat() if snapshot.as_of_date else None,
        }

    return context


def format_period_report_message(
    kind: str,
    summary: str,
    *,
    period_start: date,
    period_end: date,
) -> str:
    """Wrap plain AI text with HTML header for Telegram."""
    escaped = (
        summary.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
    if kind == "week":
        title = (
            f"🧠 <b>Анализ недели</b> "
            f"({period_start.isoformat()} — {period_end.isoformat()})"
        )
    else:
        title = f"🧠 <b>Анализ дня</b> ({period_start.isoformat()})"
    return f"{title}\n\n{escaped}"


def analyze_period(
    user: TelegramUser,
    kind: str,
    *,
    period_start: date,
    period_end: date,
    client: DeepSeekClient | None = None,
) -> tuple[str | None, dict[str, Any]]:
    """
    Call DeepSeek for day/week analysis.
    Returns (summary_text_or_None, context_dict).
    Does not check subscription — caller decides.
    """
    context = build_period_context(user, period_start=period_start, period_end=period_end)

    if not settings.DEEPSEEK_API_KEY and client is None:
        logger.info("DeepSeek skipped: DEEPSEEK_API_KEY is empty")
        return None, context

    ds = client or DeepSeekClient()
    if not ds.is_configured:
        logger.info("DeepSeek skipped: client not configured")
        return None, context

    system = SYSTEM_PROMPT_WEEK if kind == "week" else SYSTEM_PROMPT_DAY
    label = "неделю" if kind == "week" else "день"
    user_content = (
        f"Проанализируй тренировочный {label} по следующему JSON-контексту:\n"
        f"{json.dumps(context, ensure_ascii=False)}"
    )
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user_content},
    ]

    try:
        text = ds.chat(messages)
        return text, context
    except DeepSeekError:
        logger.exception(
            "DeepSeek period analysis failed for user %s kind=%s %s–%s",
            user.id,
            kind,
            period_start,
            period_end,
        )
        return None, context
