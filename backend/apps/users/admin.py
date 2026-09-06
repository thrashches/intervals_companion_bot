from django.contrib import admin

from apps.users.models import IntervalsCredentials, NotificationSettings, TelegramUser


class IntervalsCredentialsInline(admin.StackedInline):
    model = IntervalsCredentials
    extra = 0
    readonly_fields = (
        "athlete_id",
        "is_valid",
        "last_validated_at",
        "last_error",
        "api_key_encrypted",
        "created_at",
        "updated_at",
    )
    can_delete = True


class NotificationSettingsInline(admin.StackedInline):
    model = NotificationSettings
    extra = 0


@admin.register(TelegramUser)
class TelegramUserAdmin(admin.ModelAdmin):
    list_display = (
        "telegram_id",
        "username",
        "first_name",
        "timezone",
        "is_active",
        "created_at",
    )
    search_fields = ("telegram_id", "username", "first_name")
    list_filter = ("is_active", "timezone")
    inlines = [IntervalsCredentialsInline, NotificationSettingsInline]


@admin.register(IntervalsCredentials)
class IntervalsCredentialsAdmin(admin.ModelAdmin):
    list_display = ("user", "athlete_id", "is_valid", "last_validated_at")
    list_filter = ("is_valid",)
    readonly_fields = (
        "api_key_encrypted",
        "athlete_id",
        "is_valid",
        "last_validated_at",
        "last_error",
        "created_at",
        "updated_at",
    )
    actions = ["invalidate_keys"]

    @admin.action(description="Invalidate key")
    def invalidate_keys(self, request, queryset):
        queryset.update(is_valid=False, last_error="Invalidated by admin")


@admin.register(NotificationSettings)
class NotificationSettingsAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "announce_enabled",
        "announce_time",
        "report_enabled",
    )
    list_filter = ("announce_enabled", "report_enabled")
