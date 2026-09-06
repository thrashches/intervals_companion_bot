import logging

from celery import shared_task
from django.utils import timezone

from apps.intervals.models import Activity
from apps.intervals.services import (
    poll_new_activities,
    sync_calendar,
    sync_form,
    validate_credentials,
)
from apps.users.models import TelegramUser

logger = logging.getLogger(__name__)


def _active_users_qs():
    return TelegramUser.objects.filter(
        is_active=True, credentials__is_valid=True
    ).select_related("credentials", "notification_settings")


@shared_task(bind=True, max_retries=2)
def sync_user_form(self, user_id: int):
    try:
        user = TelegramUser.objects.select_related("credentials").get(pk=user_id)
        sync_form(user)
    except Exception as exc:
        logger.exception("sync_user_form failed for %s", user_id)
        raise self.retry(exc=exc, countdown=60)


@shared_task(bind=True, max_retries=2)
def sync_user_calendar(self, user_id: int):
    try:
        user = TelegramUser.objects.select_related("credentials").get(pk=user_id)
        sync_calendar(user)
    except Exception as exc:
        logger.exception("sync_user_calendar failed for %s", user_id)
        raise self.retry(exc=exc, countdown=60)


@shared_task
def sync_all_athlete_forms():
    for user in _active_users_qs():
        sync_user_form.delay(user.id)


@shared_task
def sync_all_calendars():
    for user in _active_users_qs():
        sync_user_calendar.delay(user.id)


@shared_task(bind=True, max_retries=2)
def poll_user_activities(self, user_id: int):
    from apps.notifications.tasks import send_activity_report

    try:
        user = TelegramUser.objects.select_related(
            "credentials", "notification_settings"
        ).get(pk=user_id)
        activities = poll_new_activities(user)
        settings = getattr(user, "notification_settings", None)
        if settings and not settings.report_enabled:
            return
        for activity in activities:
            if activity.report_sent_at:
                continue
            send_activity_report.delay(activity.id)
    except Exception as exc:
        logger.exception("poll_user_activities failed for %s", user_id)
        raise self.retry(exc=exc, countdown=60)


@shared_task
def poll_all_new_activities():
    for user in _active_users_qs():
        poll_user_activities.delay(user.id)


@shared_task
def validate_all_credentials():
    from apps.notifications.tasks import deliver_telegram

    qs = TelegramUser.objects.filter(is_active=True, credentials__isnull=False).select_related(
        "credentials"
    )
    for user in qs:
        ok = validate_credentials(user)
        if not ok:
            deliver_telegram.delay(
                user.telegram_id,
                "⚠️ Не удалось проверить API-ключ intervals.icu. "
                "Обновите ключ через /disconnect и /start.",
            )
