from django.db import models

from apps.users.models import TelegramUser


class NotificationLog(models.Model):
    class Kind(models.TextChoices):
        ANNOUNCE = "announce", "Announce"
        REPORT = "report", "Report"
        DAY_ANALYSIS = "day_analysis", "Day analysis"
        WEEK_ANALYSIS = "week_analysis", "Week analysis"
        SYSTEM = "system", "System"
        NEWS = "news", "News"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"

    user = models.ForeignKey(
        TelegramUser, on_delete=models.CASCADE, related_name="notification_logs"
    )
    kind = models.CharField(max_length=32, choices=Kind.choices)
    payload_ref = models.CharField(max_length=128, db_index=True)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.PENDING
    )
    telegram_message_id = models.BigIntegerField(null=True, blank=True)
    error = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Лог уведомления"
        verbose_name_plural = "Логи уведомлений"
        constraints = [
            models.UniqueConstraint(
                fields=["user", "kind", "payload_ref"],
                name="uniq_notification_per_payload",
            )
        ]

    def __str__(self) -> str:
        return f"{self.kind}:{self.payload_ref} ({self.status})"


class ServiceNews(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SENDING = "sending", "Sending"
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"

    title = models.CharField(max_length=255)
    body_md = models.TextField(
        help_text="Markdown: **bold**, *italic*, [link](url), `code`, списки",
    )
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.DRAFT
    )
    ready_to_send = models.BooleanField(
        default=False,
        help_text="Пометить к массовой рассылке (подхватит Celery beat)",
    )
    send_generation = models.PositiveIntegerField(default=1)
    sent_count = models.PositiveIntegerField(default=0)
    failed_count = models.PositiveIntegerField(default=0)
    sent_at = models.DateTimeField(null=True, blank=True)
    error = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Новость сервиса"
        verbose_name_plural = "Новости сервиса"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.title} ({self.status})"

    def payload_ref(self, *, test: bool = False, test_nonce: str = "") -> str:
        base = f"news:{self.pk}:g{self.send_generation}"
        if test:
            return f"{base}:test:{test_nonce}" if test_nonce else f"{base}:test"
        return base
