from django.contrib import admin

from apps.ai.models import AiPeriodReport


@admin.register(AiPeriodReport)
class AiPeriodReportAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "user",
        "kind",
        "period_start",
        "period_end",
        "status",
        "sent_at",
        "created_at",
    )
    list_filter = ("kind", "status", "period_start")
    search_fields = ("user__telegram_id", "user__username", "summary")
    readonly_fields = (
        "summary",
        "context_json",
        "error",
        "sent_at",
        "created_at",
        "updated_at",
    )
    autocomplete_fields = ("user",)
    actions = ["regenerate_and_send", "resend_only"]

    @admin.action(description="Regenerate and send (LLM + Telegram)")
    def regenerate_and_send(self, request, queryset):
        from apps.notifications.tasks import send_period_analysis

        count = 0
        for report in queryset.select_related("user"):
            send_period_analysis.delay(
                report.user_id,
                report.kind,
                report.period_start.isoformat(),
                report.period_end.isoformat(),
                True,  # force
                True,  # skip_subscription_check
                False,  # resend_only
            )
            count += 1
        self.message_user(request, f"Queued regenerate+send: {count}")

    @admin.action(description="Resend only (без LLM)")
    def resend_only(self, request, queryset):
        from apps.notifications.tasks import send_period_analysis

        count = 0
        skipped = 0
        for report in queryset.select_related("user"):
            if report.status != AiPeriodReport.Status.READY or not report.summary:
                skipped += 1
                continue
            send_period_analysis.delay(
                report.user_id,
                report.kind,
                report.period_start.isoformat(),
                report.period_end.isoformat(),
                False,  # force
                True,  # skip_subscription_check
                True,  # resend_only
            )
            count += 1
        msg = f"Queued resend: {count}"
        if skipped:
            msg += f", skipped (no summary): {skipped}"
        self.message_user(request, msg)
