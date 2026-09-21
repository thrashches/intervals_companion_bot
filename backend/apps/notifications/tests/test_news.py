from unittest.mock import patch

import pytest
import respx
from httpx import Response

from apps.notifications.markdown import (
    TELEGRAM_MAX_MESSAGE_LENGTH,
    format_news_message,
    markdown_to_telegram_html,
)
from apps.notifications.models import NotificationLog, ServiceNews
from apps.notifications.services import TelegramDeliveryError, deliver_text
from apps.notifications.tasks import broadcast_news, process_queued_news
from apps.users.models import TelegramUser


def test_markdown_to_telegram_html_basic():
    html = markdown_to_telegram_html("Hello **world** and *italics*")
    assert "<b>world</b>" in html
    assert "<i>italics</i>" in html
    assert "script" not in html.lower()


def test_markdown_strips_unsafe_tags():
    html = markdown_to_telegram_html("<script>alert(1)</script> **ok**")
    assert "<script>" not in html
    assert "<b>ok</b>" in html


def test_markdown_link():
    html = markdown_to_telegram_html("See [docs](https://example.com)")
    assert '<a href="https://example.com">' in html
    assert "docs" in html


def test_format_news_message_includes_title():
    text = format_news_message("Title", "Body **bold**")
    assert text.startswith("<b>Title</b>")
    assert "<b>bold</b>" in text


def test_format_news_message_truncates():
    body = "x" * (TELEGRAM_MAX_MESSAGE_LENGTH + 100)
    text = format_news_message("T", body)
    assert len(text) <= TELEGRAM_MAX_MESSAGE_LENGTH
    assert text.endswith("…")


@pytest.mark.django_db
@respx.mock
def test_deliver_text_raises_telegram_error_on_403(settings):
    settings.TELEGRAM_BOT_TOKEN = "test-token"
    respx.post("https://api.telegram.org/bottest-token/sendMessage").mock(
        return_value=Response(
            200,
            json={
                "ok": False,
                "error_code": 403,
                "description": "Forbidden: bot was blocked by the user",
            },
        )
    )
    with pytest.raises(TelegramDeliveryError) as exc_info:
        deliver_text(123, "hi")
    assert exc_info.value.is_blocked


@pytest.mark.django_db
def test_broadcast_test_only_sends_to_test_accounts():
    test_user = TelegramUser.objects.create(
        telegram_id=111, username="tester", is_test_account=True, is_active=True
    )
    TelegramUser.objects.create(
        telegram_id=222, username="regular", is_test_account=False, is_active=True
    )
    news = ServiceNews.objects.create(
        title="Hello",
        body_md="**News** body",
        status=ServiceNews.Status.DRAFT,
    )

    with patch("apps.notifications.tasks.deliver_text", return_value=99) as deliver_mock:
        with patch("apps.notifications.tasks.time.sleep"):
            result = broadcast_news(news.pk, True, test_nonce="t1")

    assert result == {"sent": 1, "failed": 0}
    assert deliver_mock.call_count == 1
    assert deliver_mock.call_args.args[0] == test_user.telegram_id
    assert NotificationLog.objects.filter(
        user=test_user,
        kind=NotificationLog.Kind.NEWS,
        status=NotificationLog.Status.SENT,
    ).exists()
    assert not NotificationLog.objects.filter(user__telegram_id=222).exists()
    news.refresh_from_db()
    assert news.ready_to_send is False
    assert news.status == ServiceNews.Status.DRAFT


@pytest.mark.django_db
def test_broadcast_all_active_users():
    active = TelegramUser.objects.create(telegram_id=1, is_active=True)
    inactive = TelegramUser.objects.create(telegram_id=2, is_active=False)
    news = ServiceNews.objects.create(
        title="Blast",
        body_md="go",
        ready_to_send=True,
        status=ServiceNews.Status.SENDING,
    )

    with patch("apps.notifications.tasks.deliver_text", return_value=1):
        with patch("apps.notifications.tasks.time.sleep"):
            result = broadcast_news(news.pk, False)

    assert result["sent"] == 1
    assert NotificationLog.objects.filter(user=active).count() == 1
    assert not NotificationLog.objects.filter(user=inactive).exists()
    news.refresh_from_db()
    assert news.status == ServiceNews.Status.SENT
    assert news.ready_to_send is False
    assert news.sent_count == 1


@pytest.mark.django_db
def test_process_queued_news_claims_and_enqueues():
    news = ServiceNews.objects.create(
        title="Q",
        body_md="body",
        ready_to_send=True,
        status=ServiceNews.Status.DRAFT,
    )
    with patch("apps.notifications.tasks.broadcast_news.delay") as delay_mock:
        process_queued_news()
    news.refresh_from_db()
    assert news.status == ServiceNews.Status.SENDING
    delay_mock.assert_called_once_with(news.pk, False)


@pytest.mark.django_db
def test_resend_uses_new_generation():
    user = TelegramUser.objects.create(telegram_id=5, is_active=True)
    news = ServiceNews.objects.create(
        title="Again",
        body_md="v1",
        status=ServiceNews.Status.SENT,
        send_generation=1,
    )
    with patch("apps.notifications.tasks.deliver_text", return_value=1):
        with patch("apps.notifications.tasks.time.sleep"):
            broadcast_news(news.pk, False)

    assert (
        NotificationLog.objects.filter(
            user=user, payload_ref=f"news:{news.pk}:g1"
        ).count()
        == 1
    )

    news.send_generation = 2
    news.ready_to_send = True
    news.status = ServiceNews.Status.SENDING
    news.sent_count = 0
    news.save()

    with patch("apps.notifications.tasks.deliver_text", return_value=2):
        with patch("apps.notifications.tasks.time.sleep"):
            broadcast_news(news.pk, False)

    assert (
        NotificationLog.objects.filter(
            user=user, kind=NotificationLog.Kind.NEWS
        ).count()
        == 2
    )
    assert NotificationLog.objects.filter(payload_ref=f"news:{news.pk}:g2").exists()


@pytest.mark.django_db
def test_broadcast_403_deactivates_user():
    user = TelegramUser.objects.create(telegram_id=9, is_active=True)
    news = ServiceNews.objects.create(
        title="Bye",
        body_md="x",
        status=ServiceNews.Status.SENDING,
    )

    def raise_403(*args, **kwargs):
        raise TelegramDeliveryError("Forbidden", error_code=403)

    with patch("apps.notifications.tasks.deliver_text", side_effect=raise_403):
        with patch("apps.notifications.tasks.time.sleep"):
            result = broadcast_news(news.pk, False)

    user.refresh_from_db()
    assert user.is_active is False
    assert result["failed"] == 1
    news.refresh_from_db()
    assert news.status == ServiceNews.Status.FAILED
