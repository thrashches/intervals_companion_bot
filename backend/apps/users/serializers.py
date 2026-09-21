from rest_framework import serializers

from apps.users.models import NotificationSettings, TelegramUser


class UpsertUserSerializer(serializers.Serializer):
    telegram_id = serializers.IntegerField()
    username = serializers.CharField(required=False, allow_blank=True, default="")
    first_name = serializers.CharField(required=False, allow_blank=True, default="")
    language_code = serializers.CharField(required=False, allow_blank=True, default="ru")


class ConnectSerializer(serializers.Serializer):
    api_key = serializers.CharField(min_length=8, max_length=256)


class TelegramUserSerializer(serializers.ModelSerializer):
    has_credentials = serializers.SerializerMethodField()
    credentials_valid = serializers.SerializerMethodField()
    timezone = serializers.SerializerMethodField()
    has_subscription = serializers.SerializerMethodField()
    subscription_expires_at = serializers.SerializerMethodField()

    class Meta:
        model = TelegramUser
        fields = [
            "telegram_id",
            "username",
            "first_name",
            "language_code",
            "timezone",
            "is_active",
            "has_credentials",
            "credentials_valid",
            "has_subscription",
            "subscription_expires_at",
        ]

    def get_timezone(self, obj: TelegramUser) -> str:
        return str(obj.timezone)

    def get_has_credentials(self, obj: TelegramUser) -> bool:
        return hasattr(obj, "credentials")

    def get_credentials_valid(self, obj: TelegramUser) -> bool:
        if hasattr(obj, "credentials"):
            return obj.credentials.is_valid
        return False

    def get_has_subscription(self, obj: TelegramUser) -> bool:
        return obj.has_active_subscription

    def get_subscription_expires_at(self, obj: TelegramUser):
        expires = obj.subscription_expires_at
        return expires.isoformat() if expires else None


class NotificationSettingsSerializer(serializers.ModelSerializer):
    timezone = serializers.CharField(source="user.timezone", required=False)

    class Meta:
        model = NotificationSettings
        fields = [
            "announce_enabled",
            "announce_time",
            "announce_days",
            "report_enabled",
            "period_analysis_enabled",
            "analysis_time",
            "timezone",
        ]

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data["timezone"] = str(instance.user.timezone)
        for time_key in ("announce_time", "analysis_time"):
            value = data.get(time_key)
            if value and hasattr(value, "isoformat"):
                data[time_key] = value.isoformat()
        return data

    def update(self, instance, validated_data):
        user_data = validated_data.pop("user", {})
        if "timezone" in user_data:
            instance.user.timezone = user_data["timezone"]
            instance.user.save(update_fields=["timezone", "updated_at"])
        return super().update(instance, validated_data)
