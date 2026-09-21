from django.db import models
from django.db.models import Q
from django.utils import timezone
from timezone_field import TimeZoneField

from apps.users.crypto import decrypt_value, encrypt_value


class TelegramUser(models.Model):
    telegram_id = models.BigIntegerField(unique=True, db_index=True)
    username = models.CharField(max_length=255, blank=True, default="")
    first_name = models.CharField(max_length=255, blank=True, default="")
    language_code = models.CharField(max_length=16, blank=True, default="ru")
    timezone = TimeZoneField(default="Europe/Moscow")
    is_active = models.BooleanField(default=True)
    is_test_account = models.BooleanField(
        default=False,
        help_text="Тестовый аккаунт для пробной рассылки новостей",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Telegram-пользователь"
        verbose_name_plural = "Telegram-пользователи"

    def __str__(self) -> str:
        return f"{self.telegram_id} (@{self.username or '—'})"

    def active_subscription(self) -> "Subscription | None":
        now = timezone.now()
        # Prefer prefetched/annotated data when available
        if hasattr(self, "_active_subscription_cache"):
            return self._active_subscription_cache
        if (
            hasattr(self, "_prefetched_objects_cache")
            and "subscriptions" in self._prefetched_objects_cache
        ):
            for sub in self.subscriptions.all():
                if sub.is_currently_active(now=now):
                    self._active_subscription_cache = sub
                    return sub
            self._active_subscription_cache = None
            return None
        sub = (
            self.subscriptions.filter(
                is_active=True,
                starts_at__lte=now,
                ends_at__gt=now,
            )
            .order_by("-ends_at")
            .first()
        )
        self._active_subscription_cache = sub
        return sub

    @property
    def has_active_subscription(self) -> bool:
        if hasattr(self, "has_active_subscription_ann"):
            return bool(self.has_active_subscription_ann)
        return self.active_subscription() is not None

    @property
    def subscription_expires_at(self):
        if hasattr(self, "active_subscription_ends_at"):
            return self.active_subscription_ends_at
        sub = self.active_subscription()
        return sub.ends_at if sub else None


class IntervalsCredentials(models.Model):
    user = models.OneToOneField(
        TelegramUser, on_delete=models.CASCADE, related_name="credentials"
    )
    api_key_encrypted = models.TextField()
    athlete_id = models.CharField(max_length=64, blank=True, default="")
    is_valid = models.BooleanField(default=False)
    last_validated_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Intervals credentials"
        verbose_name_plural = "Intervals credentials"

    def set_api_key(self, api_key: str) -> None:
        self.api_key_encrypted = encrypt_value(api_key.strip())

    def get_api_key(self) -> str:
        return decrypt_value(self.api_key_encrypted)

    def __str__(self) -> str:
        return f"Credentials for {self.user_id} ({'ok' if self.is_valid else 'invalid'})"


class NotificationSettings(models.Model):
    DAYS_ALL = [0, 1, 2, 3, 4, 5, 6]

    user = models.OneToOneField(
        TelegramUser, on_delete=models.CASCADE, related_name="notification_settings"
    )
    announce_enabled = models.BooleanField(default=True)
    announce_time = models.TimeField(default="08:00")
    announce_days = models.JSONField(default=list)  # 0=Mon ... 6=Sun
    report_enabled = models.BooleanField(default=True)
    period_analysis_enabled = models.BooleanField(
        default=True,
        help_text="Дневной и воскресный недельный AI-анализ",
    )
    analysis_time = models.TimeField(
        default="21:30",
        help_text="Локальное время отправки дневного/недельного анализа",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Настройки уведомлений"
        verbose_name_plural = "Настройки уведомлений"

    def save(self, *args, **kwargs):
        if not self.announce_days:
            self.announce_days = list(self.DAYS_ALL)
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"Notify settings for {self.user_id}"


class Subscription(models.Model):
    user = models.ForeignKey(
        TelegramUser, on_delete=models.CASCADE, related_name="subscriptions"
    )
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    is_active = models.BooleanField(
        default=True,
        help_text="Ручное отключение до окончания срока",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Подписка"
        verbose_name_plural = "Подписки"
        ordering = ["-ends_at"]
        indexes = [
            models.Index(fields=["user", "ends_at"]),
            models.Index(fields=["is_active", "ends_at"]),
        ]

    def __str__(self) -> str:
        status = "active" if self.is_currently_active() else "inactive"
        return f"Subscription #{self.pk} for {self.user_id} ({status})"

    def is_currently_active(self, *, now=None) -> bool:
        now = now or timezone.now()
        return self.is_active and self.starts_at <= now < self.ends_at

    @staticmethod
    def active_q(*, now=None) -> Q:
        now = now or timezone.now()
        return Q(is_active=True, starts_at__lte=now, ends_at__gt=now)
