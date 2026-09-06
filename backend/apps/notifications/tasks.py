import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from celery import shared_task
from django.conf import settings
from django.utils import timezone

from apps.charts.renderer import render_intervals_chart
from apps.intervals.models import Activity, CalendarEventCache
from apps.notifications.models import NotificationLog
from apps.notifications.services import (
    create_log_or_skip,
    deliver_text,
    format_activity_report,
    format_event_announce,
    _tg_send_photo,
)
from apps.users.models import NotificationSettings, TelegramUser

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3)
def deliver_telegram(self, telegram_id: int, text: str):
    try:
        deliver_text(telegram_id, text)
    except Exception as exc:
        logger.exception("deliver_telegram failed")
        raise self.retry(exc=exc, countdown=30)


@shared_task
def send_workout_announces():
    """Run every minute: announce today's workouts at user's local announce_time."""
    now_utc = timezone.now()
    settings_qs = NotificationSettings.objects.filter(
        announce_enabled=True,
        user__is_active=True,
        user__credentials__is_valid=True,
    ).select_related("user")

    for ns in settings_qs:
        user = ns.user
        tz_name = str(user.timezone)
        try:
            tz = ZoneInfo(tz_name)
        except Exception:
            tz = ZoneInfo("Europe/Moscow")
        local_now = now_utc.astimezone(tz)
        if local_now.weekday() not in (ns.announce_days or list(range(7))):
            continue
        announce_time = ns.announce_time
        if local_now.hour != announce_time.hour or local_now.minute != announce_time.minute:
            continue

        today = local_now.date()
        events = CalendarEventCache.objects.filter(
            user=user,
            start_date_local__date=today,
            category__iexact="WORKOUT",
        )
        for event in events:
            payload_ref = f"{event.external_id}:{today.isoformat()}"
            log = create_log_or_skip(user, NotificationLog.Kind.ANNOUNCE, payload_ref)
            if not log:
                continue
            try:
                msg_id = deliver_text(user.telegram_id, format_event_announce(event))
                log.status = NotificationLog.Status.SENT
                log.telegram_message_id = msg_id
                log.sent_at = timezone.now()
                log.save()
            except Exception as exc:
                log.status = NotificationLog.Status.FAILED
                log.error = str(exc)
                log.save()
                logger.exception("announce failed for user %s event %s", user.id, event.id)


@shared_task(bind=True, max_retries=2)
def send_activity_report(self, activity_id: int):
    try:
        activity = Activity.objects.select_related("user", "matched_event").get(pk=activity_id)
    except Activity.DoesNotExist:
        return

    user = activity.user
    settings_obj = getattr(user, "notification_settings", None)
    if settings_obj and not settings_obj.report_enabled:
        return
    if activity.report_sent_at:
        return

    payload_ref = f"activity:{activity.external_id}"
    log = create_log_or_skip(user, NotificationLog.Kind.REPORT, payload_ref)
    if not log:
        return

    try:
        chart_rel = render_intervals_chart(
            activity.external_id,
            activity.intervals_json or [],
            title=activity.name or "Intervals",
        )
        activity.chart_path = chart_rel
        activity.save(update_fields=["chart_path"])

        caption = format_activity_report(activity)
        photo_path = Path(settings.MEDIA_ROOT) / chart_rel
        if photo_path.exists():
            result = _tg_send_photo(user.telegram_id, photo_path, caption)
            msg_id = result.get("message_id")
        else:
            msg_id = deliver_text(user.telegram_id, caption)

        activity.report_sent_at = timezone.now()
        activity.save(update_fields=["report_sent_at"])
        log.status = NotificationLog.Status.SENT
        log.telegram_message_id = msg_id
        log.sent_at = timezone.now()
        log.save()
    except Exception as exc:
        log.status = NotificationLog.Status.FAILED
        log.error = str(exc)
        log.save()
        logger.exception("report failed for activity %s", activity_id)
        raise self.retry(exc=exc, countdown=60)
