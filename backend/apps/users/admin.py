from dateutil.relativedelta import relativedelta
from django import forms
from django.contrib import admin
from django.contrib.admin import SimpleListFilter
from django.db.models import Exists, OuterRef, Subquery
from django.utils import timezone
from django.utils.html import format_html

from apps.users.models import (
    IntervalsCredentials,
    NotificationSettings,
    Subscription,
    TelegramUser,
)
from apps.users.subscriptions import grant_subscription


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


class SubscriptionAdminForm(forms.ModelForm):
    class Meta:
        model = Subscription
        fields = ("user", "starts_at", "ends_at", "is_active")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Allow blank dates on create → grant_subscription fills them
        if not self.instance.pk:
            self.fields["starts_at"].required = False
            self.fields["ends_at"].required = False


class SubscriptionInlineForm(forms.ModelForm):
    class Meta:
        model = Subscription
        fields = ("starts_at", "ends_at", "is_active")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.instance.pk:
            self.fields["starts_at"].required = False
            self.fields["ends_at"].required = False
            self.fields["starts_at"].help_text = "Пусто = сейчас (или конец активной)"
            self.fields["ends_at"].help_text = "Пусто = +1 месяц от начала"


class SubscriptionInline(admin.TabularInline):
    model = Subscription
    form = SubscriptionInlineForm
    extra = 1
    fields = ("starts_at", "ends_at", "is_active", "created_at")
    readonly_fields = ("created_at",)
    show_change_link = True


class ActiveSubscriptionFilter(SimpleListFilter):
    title = "активная подписка"
    parameter_name = "has_subscription"

    def lookups(self, request, model_admin):
        return (
            ("yes", "Есть"),
            ("no", "Нет"),
        )

    def queryset(self, request, queryset):
        if self.value() == "yes":
            return queryset.filter(has_active_subscription_ann=True)
        if self.value() == "no":
            return queryset.filter(has_active_subscription_ann=False)
        return queryset


@admin.register(TelegramUser)
class TelegramUserAdmin(admin.ModelAdmin):
    list_display = (
        "telegram_id",
        "username",
        "first_name",
        "timezone",
        "subscription_status",
        "is_active",
        "is_test_account",
        "created_at",
    )
    search_fields = ("telegram_id", "username", "first_name")
    list_filter = ("is_active", "is_test_account", "timezone", ActiveSubscriptionFilter)
    list_editable = ("is_test_account",)
    inlines = [
        IntervalsCredentialsInline,
        NotificationSettingsInline,
        SubscriptionInline,
    ]
    actions = ["grant_one_month", "run_day_analysis", "run_week_analysis"]

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        now = timezone.now()
        active_subs = Subscription.objects.filter(
            user_id=OuterRef("pk"),
            is_active=True,
            starts_at__lte=now,
            ends_at__gt=now,
        )
        return qs.annotate(
            has_active_subscription_ann=Exists(active_subs),
            active_subscription_ends_at=Subquery(
                active_subs.order_by("-ends_at").values("ends_at")[:1]
            ),
        )

    @admin.display(description="Подписка", ordering="active_subscription_ends_at")
    def subscription_status(self, obj: TelegramUser) -> str:
        if getattr(obj, "has_active_subscription_ann", False):
            ends = obj.active_subscription_ends_at
            ends_str = timezone.localtime(ends).strftime("%d.%m.%Y") if ends else "?"
            return format_html('<span style="color:#0a7;">Да до {}</span>', ends_str)
        return format_html('<span style="color:#999;">Нет</span>')

    @admin.action(description="Выдать подписку на 1 месяц")
    def grant_one_month(self, request, queryset):
        for user in queryset:
            grant_subscription(user, months=1)
        self.message_user(request, f"Подписка выдана: {queryset.count()} польз.")

    @admin.action(description="Run day analysis (today)")
    def run_day_analysis(self, request, queryset):
        from zoneinfo import ZoneInfo

        from apps.notifications.tasks import send_period_analysis

        count = 0
        for user in queryset:
            try:
                tz = ZoneInfo(str(user.timezone))
            except Exception:
                tz = ZoneInfo("Europe/Moscow")
            today = timezone.now().astimezone(tz).date()
            send_period_analysis.delay(
                user.id,
                "day",
                today.isoformat(),
                None,
                True,
                True,
                False,
            )
            count += 1
        self.message_user(request, f"Queued day analysis: {count}")

    @admin.action(description="Run week analysis (current week)")
    def run_week_analysis(self, request, queryset):
        from datetime import timedelta
        from zoneinfo import ZoneInfo

        from apps.notifications.tasks import send_period_analysis

        count = 0
        for user in queryset:
            try:
                tz = ZoneInfo(str(user.timezone))
            except Exception:
                tz = ZoneInfo("Europe/Moscow")
            today = timezone.now().astimezone(tz).date()
            week_start = today - timedelta(days=today.weekday())
            week_end = week_start + timedelta(days=6)
            send_period_analysis.delay(
                user.id,
                "week",
                week_start.isoformat(),
                week_end.isoformat(),
                True,
                True,
                False,
            )
            count += 1
        self.message_user(request, f"Queued week analysis: {count}")
    def save_formset(self, request, form, formset, change):
        if formset.model is not Subscription:
            super().save_formset(request, form, formset, change)
            return

        instances = formset.save(commit=False)
        for obj in formset.deleted_objects:
            obj.delete()
        for instance in instances:
            if instance.pk:
                instance.save()
                continue
            if instance.starts_at and instance.ends_at:
                instance.save()
                continue
            grant_subscription(instance.user, months=1)
        formset.save_m2m()


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
        "period_analysis_enabled",
        "analysis_time",
    )
    list_filter = ("announce_enabled", "report_enabled", "period_analysis_enabled")


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    form = SubscriptionAdminForm
    list_display = ("id", "user", "starts_at", "ends_at", "is_active", "created_at")
    list_filter = ("is_active",)
    search_fields = ("user__telegram_id", "user__username")
    autocomplete_fields = ("user",)
    readonly_fields = ("created_at", "updated_at")

    def get_changeform_initial_data(self, request):
        now = timezone.now()
        return {
            "starts_at": now,
            "ends_at": now + relativedelta(months=1),
            "is_active": True,
        }

    def save_model(self, request, obj, form, change):
        if change:
            super().save_model(request, obj, form, change)
            return

        starts = form.cleaned_data.get("starts_at")
        ends = form.cleaned_data.get("ends_at")
        if starts and ends:
            super().save_model(request, obj, form, change)
            return

        granted = grant_subscription(obj.user, months=1)
        # Point admin change redirect at the created row
        obj.pk = granted.pk
        obj.starts_at = granted.starts_at
        obj.ends_at = granted.ends_at
        obj.is_active = granted.is_active
        obj.created_at = granted.created_at
        obj.updated_at = granted.updated_at
