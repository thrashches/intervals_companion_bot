from django.db import models

from apps.users.models import TelegramUser


class AiPeriodReport(models.Model):
    class Kind(models.TextChoices):
        DAY = "day", "Day"
        WEEK = "week", "Week"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        READY = "ready", "Ready"
        FAILED = "failed", "Failed"

    user = models.ForeignKey(
        TelegramUser, on_delete=models.CASCADE, related_name="ai_period_reports"
    )
    kind = models.CharField(max_length=16, choices=Kind.choices)
    period_start = models.DateField()
    period_end = models.DateField()
    summary = models.TextField(blank=True, default="")
    context_json = models.JSONField(default=dict, blank=True)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.PENDING
    )
    error = models.TextField(blank=True, default="")
    sent_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "AI-анализ периода"
        verbose_name_plural = "AI-анализы периодов"
        constraints = [
            models.UniqueConstraint(
                fields=["user", "kind", "period_start", "period_end"],
                name="uniq_ai_period_report",
            )
        ]
        indexes = [
            models.Index(fields=["user", "kind", "-period_start"]),
            models.Index(fields=["status"]),
        ]
        ordering = ["-period_start", "-created_at"]

    def __str__(self) -> str:
        return (
            f"{self.kind} {self.period_start}…{self.period_end} "
            f"user={self.user_id} ({self.status})"
        )

    @property
    def payload_ref(self) -> str:
        return f"{self.kind}:{self.period_start.isoformat()}:{self.period_end.isoformat()}"
