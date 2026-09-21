from django import forms
from django.contrib import admin
from django.utils.html import format_html
from django.utils.safestring import mark_safe

from apps.notifications.markdown import format_news_message
from apps.notifications.models import NotificationLog, ServiceNews


@admin.register(NotificationLog)
class NotificationLogAdmin(admin.ModelAdmin):
    list_display = ("user", "kind", "payload_ref", "status", "created_at", "sent_at")
    list_filter = ("kind", "status")
    search_fields = ("payload_ref", "user__telegram_id", "user__username")
    readonly_fields = ("created_at", "sent_at", "telegram_message_id", "error")


class ServiceNewsAdminForm(forms.ModelForm):
    class Meta:
        model = ServiceNews
        fields = (
            "title",
            "body_md",
            "ready_to_send",
            "status",
            "send_generation",
        )
        widgets = {
            "body_md": forms.Textarea(attrs={"rows": 16, "cols": 80}),
        }


@admin.register(ServiceNews)
class ServiceNewsAdmin(admin.ModelAdmin):
    form = ServiceNewsAdminForm
    list_display = (
        "id",
        "title",
        "status",
        "ready_to_send",
        "send_generation",
        "sent_count",
        "failed_count",
        "sent_at",
        "created_at",
    )
    list_filter = ("status", "ready_to_send")
    search_fields = ("title", "body_md")
    readonly_fields = (
        "preview_html",
        "send_generation",
        "sent_count",
        "failed_count",
        "sent_at",
        "error",
        "created_at",
        "updated_at",
    )
    actions = ["send_to_test_accounts", "mark_ready_to_send", "resend_to_all"]

    fieldsets = (
        (
            None,
            {
                "fields": (
                    "title",
                    "body_md",
                    "preview_html",
                    "ready_to_send",
                    "status",
                )
            },
        ),
        (
            "Доставка",
            {
                "fields": (
                    "send_generation",
                    "sent_count",
                    "failed_count",
                    "sent_at",
                    "error",
                    "created_at",
                    "updated_at",
                )
            },
        ),
    )

    @admin.display(description="Превью (Telegram HTML)")
    def preview_html(self, obj: ServiceNews) -> str:
        if not obj or not (obj.title or obj.body_md):
            return "—"
        rendered = format_news_message(obj.title, obj.body_md)
        return format_html(
            '<pre style="white-space:pre-wrap;max-width:720px;">{}</pre>'
            '<div style="border:1px solid #ccc;padding:12px;max-width:720px;'
            'margin-top:8px;background:#fafafa;">{}</div>',
            rendered,
            mark_safe(rendered),
        )

    @admin.action(description="Отправить тестовым аккаунтам")
    def send_to_test_accounts(self, request, queryset):
        from apps.notifications.tasks import broadcast_news

        count = 0
        for news in queryset:
            broadcast_news.delay(news.pk, True)
            count += 1
        self.message_user(request, f"Тестовая отправка поставлена в очередь: {count}")

    @admin.action(description="Пометить к отправке")
    def mark_ready_to_send(self, request, queryset):
        updated = 0
        for news in queryset:
            if news.status == ServiceNews.Status.SENDING:
                continue
            news.ready_to_send = True
            if news.status != ServiceNews.Status.SENDING:
                news.status = ServiceNews.Status.DRAFT
            news.error = ""
            news.save(update_fields=["ready_to_send", "status", "error", "updated_at"])
            updated += 1
        self.message_user(request, f"Помечено к отправке: {updated}")

    @admin.action(description="Отправить заново")
    def resend_to_all(self, request, queryset):
        updated = 0
        for news in queryset:
            if news.status == ServiceNews.Status.SENDING:
                continue
            news.send_generation = (news.send_generation or 1) + 1
            news.ready_to_send = True
            news.status = ServiceNews.Status.DRAFT
            news.sent_count = 0
            news.failed_count = 0
            news.error = ""
            news.sent_at = None
            news.save(
                update_fields=[
                    "send_generation",
                    "ready_to_send",
                    "status",
                    "sent_count",
                    "failed_count",
                    "error",
                    "sent_at",
                    "updated_at",
                ]
            )
            updated += 1
        self.message_user(request, f"Повторная отправка поставлена в очередь: {updated}")
