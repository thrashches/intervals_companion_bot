import logging
import time
from datetime import date, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from celery import shared_task
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.charts.renderer import render_intervals_chart
from apps.intervals.models import Activity, CalendarEventCache
from apps.notifications.markdown import format_news_message
from apps.notifications.models import NotificationLog, ServiceNews
from apps.notifications.services import (
    TelegramDeliveryError,
    create_log_or_skip,
    deliver_text,
    format_activity_report,
    format_event_announce,
    _tg_send_photo,
)
from apps.users.models import NotificationSettings, TelegramUser

logger = logging.getLogger(__name__)

# Stay under Telegram free broadcast ceiling (~30 msg/s).
_BROADCAST_DELAY_SEC = 0.04


def _user_local_now(user: TelegramUser, now_utc=None):
    now_utc = now_utc or timezone.now()
    try:
        tz = ZoneInfo(str(user.timezone))
    except Exception:
        tz = ZoneInfo("Europe/Moscow")
    return now_utc.astimezone(tz)


def _analysis_log_kind(kind: str) -> str:
    if kind == "week":
        return NotificationLog.Kind.WEEK_ANALYSIS
    return NotificationLog.Kind.DAY_ANALYSIS


def _period_payload_ref(kind: str, period_start: date, period_end: date) -> str:
    return f"{kind}:{period_start.isoformat()}:{period_end.isoformat()}"


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
        activity = Activity.objects.select_related(
            "user",
            "matched_event",
            "user__athlete_snapshot",
        ).get(pk=activity_id)
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


@shared_task
def send_period_analyses():
    """Every minute: day (and Sunday week) AI analysis at user's analysis_time."""
    now_utc = timezone.now()
    settings_qs = NotificationSettings.objects.filter(
        period_analysis_enabled=True,
        user__is_active=True,
        user__credentials__is_valid=True,
    ).select_related("user")

    for ns in settings_qs:
        user = ns.user
        if not user.has_active_subscription:
            continue
        local_now = _user_local_now(user, now_utc)
        analysis_time = ns.analysis_time
        if local_now.hour != analysis_time.hour or local_now.minute != analysis_time.minute:
            continue

        today = local_now.date()
        send_period_analysis.delay(user.id, "day", today.isoformat())
        # Sunday = weekday 6
        if local_now.weekday() == 6:
            week_start = today - timedelta(days=6)  # Mon of current week
            send_period_analysis.delay(
                user.id,
                "week",
                week_start.isoformat(),
                today.isoformat(),
            )


@shared_task(bind=True, max_retries=2)
def send_period_analysis(
    self,
    user_id: int,
    kind: str,
    period_start: str,
    period_end: str | None = None,
    force: bool = False,
    skip_subscription_check: bool = False,
    resend_only: bool = False,
):
    """
    Generate (unless resend_only) and deliver day/week AI analysis.
    force=True regenerates and re-sends even if already delivered.
    skip_subscription_check=True for admin actions.
    """
    from apps.ai.analysis import (
        analyze_period,
        format_period_report_message,
        resolve_period_dates,
    )
    from apps.ai.models import AiPeriodReport
    from apps.intervals.services import poll_new_activities, sync_calendar, sync_form

    try:
        user = TelegramUser.objects.select_related(
            "credentials",
            "athlete_snapshot",
            "notification_settings",
        ).get(pk=user_id)
    except TelegramUser.DoesNotExist:
        return

    if not skip_subscription_check and not user.has_active_subscription:
        logger.info("Period analysis skipped: no subscription user=%s", user_id)
        return

    start_d = date.fromisoformat(period_start)
    end_d = date.fromisoformat(period_end) if period_end else None
    start_d, end_d = resolve_period_dates(
        kind, period_start=start_d, period_end=end_d, today=start_d
    )

    report, _ = AiPeriodReport.objects.get_or_create(
        user=user,
        kind=kind,
        period_start=start_d,
        period_end=end_d,
        defaults={"status": AiPeriodReport.Status.PENDING},
    )

    log_kind = _analysis_log_kind(kind)
    payload_ref = _period_payload_ref(kind, start_d, end_d)

    if force or resend_only:
        NotificationLog.objects.filter(
            user=user, kind=log_kind, payload_ref=payload_ref
        ).delete()

    if not force and not resend_only:
        if report.status == AiPeriodReport.Status.READY and report.sent_at:
            return
        existing = NotificationLog.objects.filter(
            user=user,
            kind=log_kind,
            payload_ref=payload_ref,
            status=NotificationLog.Status.SENT,
        ).exists()
        if existing:
            return

    log = create_log_or_skip(user, log_kind, payload_ref)
    if not log:
        # Race / already pending — if force cleared logs this shouldn't happen
        if not force and not resend_only:
            return
        log = NotificationLog.objects.create(
            user=user,
            kind=log_kind,
            payload_ref=payload_ref,
            status=NotificationLog.Status.PENDING,
        )

    try:
        if resend_only:
            if report.status != AiPeriodReport.Status.READY or not report.summary:
                log.status = NotificationLog.Status.FAILED
                log.error = "No ready summary to resend"
                log.save(update_fields=["status", "error"])
                return
            summary = report.summary
        else:
            # Refresh data for the period
            lookback = max(3, (date.today() - start_d).days + 1)
            try:
                sync_calendar(user, past_days=max(lookback, 7), future_days=3)
            except Exception:
                logger.exception("calendar sync before analysis failed user=%s", user_id)
            try:
                sync_form(user)
            except Exception:
                logger.exception("form/wellness sync before analysis failed user=%s", user_id)
            try:
                poll_new_activities(user, lookback_days=lookback)
            except Exception:
                logger.exception("activity poll before analysis failed user=%s", user_id)

            report.status = AiPeriodReport.Status.PENDING
            report.error = ""
            report.save(update_fields=["status", "error", "updated_at"])

            summary, context = analyze_period(
                user, kind, period_start=start_d, period_end=end_d
            )
            report.context_json = context
            if not summary:
                report.status = AiPeriodReport.Status.FAILED
                report.error = "Empty AI response or DeepSeek unavailable"
                report.save(
                    update_fields=["context_json", "status", "error", "updated_at"]
                )
                log.status = NotificationLog.Status.FAILED
                log.error = report.error
                log.save(update_fields=["status", "error"])
                return

            report.summary = summary
            report.status = AiPeriodReport.Status.READY
            report.error = ""
            report.save(
                update_fields=[
                    "summary",
                    "context_json",
                    "status",
                    "error",
                    "updated_at",
                ]
            )

        text = format_period_report_message(
            kind, summary, period_start=start_d, period_end=end_d
        )
        msg_id = deliver_text(user.telegram_id, text)
        report.sent_at = timezone.now()
        report.save(update_fields=["sent_at", "updated_at"])
        log.status = NotificationLog.Status.SENT
        log.telegram_message_id = msg_id
        log.sent_at = timezone.now()
        log.error = ""
        log.save()

        if kind == "week":
            _send_week_charts(user, start_d, end_d)
    except Exception as exc:
        report.status = AiPeriodReport.Status.FAILED
        report.error = str(exc)[:2000]
        report.save(update_fields=["status", "error", "updated_at"])
        log.status = NotificationLog.Status.FAILED
        log.error = str(exc)[:2000]
        log.save(update_fields=["status", "error"])
        logger.exception(
            "period analysis failed user=%s kind=%s %s–%s",
            user_id,
            kind,
            start_d,
            end_d,
        )
        raise self.retry(exc=exc, countdown=60)


def _send_week_charts(user: TelegramUser, period_start: date, period_end: date) -> None:
    """Best-effort: attach form trend + zone distribution after weekly AI text."""
    from apps.intervals.services import form_payload, zones_payload

    try:
        form = form_payload(user, with_chart=True)
        chart_path = form.get("chart_path")
        if chart_path:
            full = Path(settings.MEDIA_ROOT) / chart_path
            if full.is_file():
                _tg_send_photo(
                    user.telegram_id,
                    full,
                    (form.get("chart_caption") or "CTL / ATL / TSB (30 дней)")[:1024],
                )
    except Exception:
        logger.exception("week form chart failed user=%s", user.id)

    try:
        zones = zones_payload(user, period_start=period_start, period_end=period_end)
        chart_path = zones.get("chart_path")
        if chart_path:
            full = Path(settings.MEDIA_ROOT) / chart_path
            if full.is_file():
                _tg_send_photo(
                    user.telegram_id,
                    full,
                    (zones.get("caption") or "Зоны за неделю")[:1024],
                )
    except Exception:
        logger.exception("week zones chart failed user=%s", user.id)


@shared_task
def process_queued_news():
    """Pick news marked ready_to_send and enqueue full broadcast."""
    qs = ServiceNews.objects.filter(
        ready_to_send=True,
    ).exclude(status=ServiceNews.Status.SENDING)
    for news in qs:
        claimed = (
            ServiceNews.objects.filter(
                pk=news.pk,
                ready_to_send=True,
            )
            .exclude(status=ServiceNews.Status.SENDING)
            .update(status=ServiceNews.Status.SENDING, error="")
        )
        if claimed:
            broadcast_news.delay(news.pk, False)


@shared_task(bind=True, max_retries=5)
def broadcast_news(self, news_id: int, test_only: bool = False, test_nonce: str = ""):
    """
    Send service news to test accounts or all active users.
    test_only=True does not finalize ready_to_send / mass status.
    """
    try:
        news = ServiceNews.objects.get(pk=news_id)
    except ServiceNews.DoesNotExist:
        return

    if test_only:
        users = TelegramUser.objects.filter(is_test_account=True)
        payload_ref = news.payload_ref(
            test=True, test_nonce=test_nonce or str(int(time.time()))
        )
    else:
        users = TelegramUser.objects.filter(is_active=True)
        payload_ref = news.payload_ref()
        if news.status != ServiceNews.Status.SENDING:
            ServiceNews.objects.filter(pk=news.pk).update(
                status=ServiceNews.Status.SENDING, error=""
            )
            news.refresh_from_db()

    text = format_news_message(news.title, news.body_md)
    sent = 0
    failed = 0

    for user in users.iterator():
        log = create_log_or_skip(user, NotificationLog.Kind.NEWS, payload_ref)
        if not log:
            continue
        try:
            msg_id = deliver_text(user.telegram_id, text)
            log.status = NotificationLog.Status.SENT
            log.telegram_message_id = msg_id
            log.sent_at = timezone.now()
            log.error = ""
            log.save()
            sent += 1
        except TelegramDeliveryError as exc:
            log.status = NotificationLog.Status.FAILED
            log.error = str(exc)[:2000]
            log.save(update_fields=["status", "error"])
            failed += 1
            if exc.is_blocked:
                TelegramUser.objects.filter(pk=user.pk, is_active=True).update(
                    is_active=False
                )
                logger.info(
                    "Deactivated user %s after Telegram 403 during news %s",
                    user.pk,
                    news_id,
                )
            elif exc.is_rate_limited:
                countdown = int(exc.retry_after or 5)
                logger.warning(
                    "Rate limited during news %s, retry in %ss", news_id, countdown
                )
                # Drop log so retry can re-attempt this user; already-SENT stay skipped.
                log.delete()
                failed -= 1
                raise self.retry(exc=exc, countdown=max(countdown, 1))
            else:
                logger.exception(
                    "news deliver failed user=%s news=%s", user.pk, news_id
                )
        except Exception as exc:
            log.status = NotificationLog.Status.FAILED
            log.error = str(exc)[:2000]
            log.save(update_fields=["status", "error"])
            failed += 1
            logger.exception("news deliver failed user=%s news=%s", user.pk, news_id)

        time.sleep(_BROADCAST_DELAY_SEC)

    if test_only:
        logger.info(
            "Test broadcast news=%s sent=%s failed=%s", news_id, sent, failed
        )
        return {"sent": sent, "failed": failed}

    logs = NotificationLog.objects.filter(
        kind=NotificationLog.Kind.NEWS,
        payload_ref=payload_ref,
    )
    total_sent = logs.filter(status=NotificationLog.Status.SENT).count()
    total_failed = logs.filter(status=NotificationLog.Status.FAILED).count()
    last_failed_error = (
        logs.filter(status=NotificationLog.Status.FAILED)
        .order_by("-id")
        .values_list("error", flat=True)
        .first()
        or ""
    )

    with transaction.atomic():
        n = ServiceNews.objects.select_for_update().get(pk=news.pk)
        n.sent_count = total_sent
        n.failed_count = total_failed
        n.ready_to_send = False
        n.sent_at = timezone.now()
        if total_failed and not total_sent:
            n.status = ServiceNews.Status.FAILED
            n.error = last_failed_error or "All deliveries failed"
        else:
            n.status = ServiceNews.Status.SENT
            n.error = last_failed_error if total_failed else ""
        n.save(
            update_fields=[
                "sent_count",
                "failed_count",
                "ready_to_send",
                "sent_at",
                "status",
                "error",
                "updated_at",
            ]
        )

    return {"sent": total_sent, "failed": total_failed}
