from django.db import models
from timezone_field import TimeZoneField

from apps.users.crypto import decrypt_value, encrypt_value


class TelegramUser(models.Model):
    telegram_id = models.BigIntegerField(unique=True, db_index=True)
    username = models.CharField(max_length=255, blank=True, default="")
    first_name = models.CharField(max_length=255, blank=True, default="")
    language_code = models.CharField(max_length=16, blank=True, default="ru")
    timezone = TimeZoneField(default="Europe/Moscow")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Telegram-пользователь"
        verbose_name_plural = "Telegram-пользователи"

    def __str__(self) -> str:
        return f"{self.telegram_id} (@{self.username or '—'})"


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
    morning_summary_enabled = models.BooleanField(default=False)
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
