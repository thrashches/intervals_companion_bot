from django.contrib import admin

from apps.notifications.models import NotificationLog


@admin.register(NotificationLog)
class NotificationLogAdmin(admin.ModelAdmin):
    list_display = ("user", "kind", "payload_ref", "status", "created_at", "sent_at")
    list_filter = ("kind", "status")
    search_fields = ("payload_ref", "user__telegram_id", "user__username")
    readonly_fields = ("created_at", "sent_at", "telegram_message_id", "error")
