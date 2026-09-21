from datetime import datetime

from dateutil.relativedelta import relativedelta
from django.utils import timezone

from apps.users.models import Subscription, TelegramUser


def grant_subscription(
    user: TelegramUser,
    *,
    months: int = 1,
    starts_at: datetime | None = None,
) -> Subscription:
    """Create a subscription period. Defaults to 1 month, stacked after any active one."""
    now = timezone.now()
    if starts_at is None:
        current = user.active_subscription()
        if current is not None:
            starts_at = max(now, current.ends_at)
        else:
            starts_at = now
    ends_at = starts_at + relativedelta(months=months)
    sub = Subscription.objects.create(
        user=user,
        starts_at=starts_at,
        ends_at=ends_at,
        is_active=True,
    )
    if hasattr(user, "_active_subscription_cache"):
        delattr(user, "_active_subscription_cache")
    return sub
