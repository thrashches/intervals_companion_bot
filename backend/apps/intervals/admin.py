from django.contrib import admin

from apps.intervals.models import Activity, AthleteSnapshot, CalendarEventCache


@admin.register(AthleteSnapshot)
class AthleteSnapshotAdmin(admin.ModelAdmin):
    list_display = ("user", "fitness", "fatigue", "form", "weight", "vo2max", "as_of_date")
    readonly_fields = ("raw_json", "synced_at")


@admin.register(CalendarEventCache)
class CalendarEventCacheAdmin(admin.ModelAdmin):
    list_display = ("user", "name", "type", "category", "start_date_local", "icu_training_load")
    list_filter = ("category", "type")
    search_fields = ("name", "external_id")
    readonly_fields = ("raw_json", "workout_doc", "synced_at")
    actions = ["force_resync"]

    @admin.action(description="Force sync calendar for selected users")
    def force_resync(self, request, queryset):
        from apps.intervals.tasks import sync_user_calendar

        user_ids = set(queryset.values_list("user_id", flat=True))
        for uid in user_ids:
            sync_user_calendar.delay(uid)


@admin.register(Activity)
class ActivityAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "name",
        "type",
        "start_date_local",
        "icu_training_load",
        "compliance",
        "report_sent_at",
    )
    list_filter = ("type",)
    search_fields = ("name", "external_id")
    readonly_fields = ("raw_json", "intervals_json", "synced_at", "chart_path")
    actions = ["resend_report"]

    @admin.action(description="Resend report")
    def resend_report(self, request, queryset):
        from apps.notifications.tasks import send_activity_report

        for activity in queryset:
            activity.report_sent_at = None
            activity.save(update_fields=["report_sent_at"])
            send_activity_report.delay(activity.id)
