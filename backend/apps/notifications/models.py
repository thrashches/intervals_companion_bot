from django.db import models

from apps.users.models import TelegramUser


class NotificationLog(models.Model):
    class Kind(models.TextChoices):
        ANNOUNCE = "announce", "Announce"
        REPORT = "report", "Report"
        SYSTEM = "system", "System"

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
