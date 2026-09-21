from dateutil.relativedelta import relativedelta
from django.utils import timezone

import pytest

from apps.users.models import TelegramUser
from apps.users.serializers import TelegramUserSerializer
from apps.users.subscriptions import grant_subscription


@pytest.mark.django_db
def test_grant_subscription_default_one_month():
    user = TelegramUser.objects.create(telegram_id=1001, username="sub_user")
    assert user.has_active_subscription is False
    assert user.subscription_expires_at is None

    before = timezone.now()
    sub = grant_subscription(user)
    after = timezone.now()

    assert user.has_active_subscription is True
    assert sub.is_currently_active()
    assert before <= sub.starts_at <= after
    assert sub.ends_at == sub.starts_at + relativedelta(months=1)
    assert user.subscription_expires_at == sub.ends_at


@pytest.mark.django_db
def test_grant_subscription_stacks_after_active():
    user = TelegramUser.objects.create(telegram_id=1002)
    first = grant_subscription(user, months=1)
    second = grant_subscription(user, months=1)

    assert second.starts_at == first.ends_at
    assert second.ends_at == first.ends_at + relativedelta(months=1)
    assert user.active_subscription() == first


@pytest.mark.django_db
def test_telegram_user_serializer_subscription_fields():
    user = TelegramUser.objects.create(telegram_id=1003)
    data = TelegramUserSerializer(user).data
    assert data["has_subscription"] is False
    assert data["subscription_expires_at"] is None

    sub = grant_subscription(user)
    data = TelegramUserSerializer(user).data
    assert data["has_subscription"] is True
    assert data["subscription_expires_at"] == sub.ends_at.isoformat()
