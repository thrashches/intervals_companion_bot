from __future__ import annotations

import logging
import re
from pathlib import Path

import httpx
from django.conf import settings
from django.db import IntegrityError, transaction

from apps.intervals.models import Activity, CalendarEventCache
from apps.notifications.models import NotificationLog
from apps.users.models import TelegramUser

logger = logging.getLogger(__name__)


class TelegramDeliveryError(Exception):
    """Raised when Telegram Bot API rejects or rate-limits a message."""

    def __init__(
        self,
        message: str,
        *,
        error_code: int | None = None,
        retry_after: float | None = None,
    ):
        super().__init__(message)
        self.error_code = error_code
        self.retry_after = retry_after

    @property
    def is_blocked(self) -> bool:
        return self.error_code == 403

    @property
    def is_rate_limited(self) -> bool:
        return self.error_code == 429


def _parse_retry_after(data: dict, description: str) -> float | None:
    params = data.get("parameters") or {}
    if isinstance(params, dict) and params.get("retry_after") is not None:
        try:
            return float(params["retry_after"])
        except (TypeError, ValueError):
            pass
    match = re.search(r"retry after (\d+)", description or "", re.IGNORECASE)
    if match:
        return float(match.group(1))
    return None


def _tg_api(method: str, **payload):
    token = settings.TELEGRAM_BOT_TOKEN
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured")
    url = f"https://api.telegram.org/bot{token}/{method}"
    with httpx.Client(timeout=60.0) as client:
        response = client.post(url, json=payload)
        try:
            data = response.json()
        except ValueError:
            raise TelegramDeliveryError(
                response.text or f"HTTP {response.status_code}",
                error_code=response.status_code,
            ) from None
        if not data.get("ok"):
            description = data.get("description") or response.text
            error_code = data.get("error_code") or response.status_code
            retry_after = _parse_retry_after(data, description)
            raise TelegramDeliveryError(
                description,
                error_code=error_code,
                retry_after=retry_after,
            )
        return data.get("result") or {}


def _tg_send_photo(chat_id: int, photo_path: Path, caption: str) -> dict:
    token = settings.TELEGRAM_BOT_TOKEN
    url = f"https://api.telegram.org/bot{token}/sendPhoto"
    with httpx.Client(timeout=120.0) as client:
        with photo_path.open("rb") as fh:
            response = client.post(
                url,
                data={
                    "chat_id": chat_id,
                    "caption": caption[:1024],
                    "parse_mode": "HTML",
                },
                files={"photo": fh},
            )
        try:
            data = response.json()
        except ValueError:
            raise TelegramDeliveryError(
                response.text or f"HTTP {response.status_code}",
                error_code=response.status_code,
            ) from None
        if not data.get("ok"):
            description = data.get("description") or response.text
            error_code = data.get("error_code") or response.status_code
            retry_after = _parse_retry_after(data, description)
            raise TelegramDeliveryError(
                description,
                error_code=error_code,
                retry_after=retry_after,
            )
        return data.get("result") or {}


def deliver_text(telegram_id: int, text: str) -> int | None:
    result = _tg_api("sendMessage", chat_id=telegram_id, text=text, parse_mode="HTML")
    return result.get("message_id")


def format_duration(seconds: int | None) -> str:
    if not seconds:
        return "—"
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}ч {m:02d}м"
    if m and s:
        return f"{m}м {s:02d}с"
    if m:
        return f"{m}м"
    return f"{s}с"


def _format_power_target(step: dict) -> str:
    power = step.get("power")
    if isinstance(power, (int, float)):
        return f"{power:.0f} W"
    if not isinstance(power, dict):
        return ""
    units = str(power.get("units") or "").lower()
    if units in ("%ftp", "ftp", "%"):
        start, end, value = power.get("start"), power.get("end"), power.get("value")
        if start is not None and end is not None:
            return f"{float(start):.0f}–{float(end):.0f}% FTP"
        if value is not None:
            return f"{float(value):.0f}% FTP"
    if units in ("power_zone", "zone"):
        z = power.get("value")
        return f"Z{z}" if z is not None else ""
    if units in ("w", "watts", "watt"):
        start, end, value = power.get("start"), power.get("end"), power.get("value")
        if start is not None and end is not None:
            return f"{float(start):.0f}–{float(end):.0f} W"
        if value is not None:
            return f"{float(value):.0f} W"
    return ""


def _format_one_step(step: dict) -> str:
    label = (step.get("text") or "").strip()
    duration = step.get("duration")
    dur = format_duration(duration) if duration else ""
    target = _format_power_target(step)
    parts = [p for p in (dur, target) if p]
    body = " ".join(parts) if parts else "интервал"
    if label and label.lower() not in body.lower():
        return f"{label}: {body}"
    return body


def format_workout_steps(workout_doc: dict | None, max_lines: int = 12) -> str:
    """Human-readable planned intervals for captions."""
    doc = workout_doc or {}
    steps = doc.get("steps") or []
    if not steps:
        return ""

    lines: list[str] = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        nested = step.get("steps")
        reps = step.get("reps") or step.get("repeat")
        if nested:
            inner = [_format_one_step(s) for s in nested if isinstance(s, dict)]
            inner_txt = " → ".join(inner) if inner else "блок"
            prefix = f"{int(reps)}× " if reps else ""
            text = (step.get("text") or "").strip()
            if text and not str(reps):
                lines.append(f"• {text}: {inner_txt}")
            else:
                lines.append(f"• {prefix}{inner_txt}")
        else:
            lines.append(f"• {_format_one_step(step)}")
        if len(lines) >= max_lines:
            remaining = len(steps) - max_lines
            if remaining > 0:
                lines.append(f"• …ещё {remaining}")
            break

    return "\n".join(lines)


def format_event_announce(event: CalendarEventCache) -> str:
    load = f"\nНагрузка: {event.icu_training_load:.0f}" if event.icu_training_load else ""
    doc = event.workout_doc or {}
    duration = doc.get("duration")
    duration_line = f"\nДлительность: {format_duration(duration)}" if duration else ""
    steps_block = format_workout_steps(doc)
    steps_preview = f"\n\nИнтервалы:\n{steps_block}" if steps_block else ""
    return (
        f"🚴 <b>Анонс тренировки</b>\n"
        f"<b>{event.name or 'Тренировка'}</b>\n"
        f"Тип: {event.type or '—'}"
        f"{duration_line}{load}{steps_preview}"
    )


def format_plan_event(event: CalendarEventCache | dict) -> str:
    """Caption for a planned workout chart message."""
    if isinstance(event, dict):
        name = event.get("name") or "Тренировка"
        ev_type = event.get("type") or "—"
        load_val = event.get("icu_training_load")
        start = event.get("start_date_local")
        doc = event.get("workout_doc") or {}
    else:
        name = event.name or "Тренировка"
        ev_type = event.type or "—"
        load_val = event.icu_training_load
        start = event.start_date_local.isoformat() if event.start_date_local else None
        doc = event.workout_doc or {}

    date_line = ""
    if start:
        date_str = str(start)[:10]
        date_line = f"\nДата: {date_str}"
    load = f"\nНагрузка: {load_val:.0f}" if load_val is not None else ""
    duration = doc.get("duration")
    duration_line = f"\nДлительность: {format_duration(duration)}" if duration else ""
    steps_block = format_workout_steps(doc)
    steps_preview = f"\n\nИнтервалы:\n{steps_block}" if steps_block else ""
    text = (
        f"📅 <b>План тренировки</b>\n"
        f"<b>{name}</b>\n"
        f"Тип: {ev_type}"
        f"{date_line}{duration_line}{load}{steps_preview}"
    )
    return text[:1024]



def _format_curve_pr_line(pr: dict) -> str | None:
    metric = pr.get("metric")
    label = pr.get("label") or ""
    value = pr.get("value")
    if value is None:
        return None
    try:
        value_f = float(value)
    except (TypeError, ValueError):
        return None
    if metric == "power":
        return f"🏆 Новый рекорд: мощность {label} — {value_f:.0f} W"
    if metric == "hr":
        return f"🏆 Новый рекорд: пульс {label} — {value_f:.0f} bpm"
    return f"🏆 Новый рекорд: {metric} {label} — {value_f:.0f}"


def format_activity_report(activity: Activity) -> str:
    compliance = (
        f"{activity.compliance:.0f}%" if activity.compliance is not None else "—"
    )
    lines = [
        "🏁 <b>Отчёт о тренировке</b>",
        f"<b>{activity.name or 'Активность'}</b>",
        f"Тип: {activity.type or '—'}",
        f"Длительность: {format_duration(activity.moving_time)}",
    ]
    if activity.distance:
        lines.append(f"Дистанция: {activity.distance / 1000:.1f} км")
    if activity.icu_training_load is not None:
        lines.append(f"Нагрузка (TSS/Load): {activity.icu_training_load:.0f}")
    if activity.icu_intensity is not None:
        lines.append(f"Интенсивность: {activity.icu_intensity:.0f}")
    lines.append(f"Соответствие плану: {compliance}")
    if activity.weighted_average_watts:
        lines.append(f"NP/взвеш. мощность: {activity.weighted_average_watts:.0f} W")
    elif activity.average_watts:
        lines.append(f"Средняя мощность: {activity.average_watts:.0f} W")
    if activity.average_heartrate:
        lines.append(f"Средний пульс: {activity.average_heartrate:.0f} bpm")
    if activity.max_heartrate:
        lines.append(f"Макс. пульс: {activity.max_heartrate:.0f} bpm")
    if activity.total_elevation_gain:
        lines.append(f"Набор: {activity.total_elevation_gain:.0f} м")
    if activity.matched_event:
        lines.append(f"План: {activity.matched_event.name}")
    curve_prs = activity.curve_prs if isinstance(activity.curve_prs, list) else []
    for pr in curve_prs:
        if isinstance(pr, dict):
            line = _format_curve_pr_line(pr)
            if line:
                lines.append(line)
    return "\n".join(lines)


def create_log_or_skip(
    user: TelegramUser, kind: str, payload_ref: str
) -> NotificationLog | None:
    try:
        with transaction.atomic():
            return NotificationLog.objects.create(
                user=user,
                kind=kind,
                payload_ref=payload_ref,
                status=NotificationLog.Status.PENDING,
            )
    except IntegrityError:
        return None
