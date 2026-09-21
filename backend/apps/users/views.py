from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.intervals.client import IntervalsAPIError, IntervalsClient
from apps.users.models import IntervalsCredentials, NotificationSettings, TelegramUser
from apps.users.permissions import HasInternalToken
from apps.users.serializers import (
    ConnectSerializer,
    NotificationSettingsSerializer,
    TelegramUserSerializer,
    UpsertUserSerializer,
)


def get_user_or_404(telegram_id: int) -> TelegramUser | None:
    try:
        return (
            TelegramUser.objects.select_related("credentials", "notification_settings")
            .prefetch_related("subscriptions")
            .get(telegram_id=telegram_id)
        )
    except TelegramUser.DoesNotExist:
        return None


class UpsertUserView(APIView):
    permission_classes = [HasInternalToken]

    def post(self, request):
        serializer = UpsertUserSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        user, _ = TelegramUser.objects.update_or_create(
            telegram_id=data["telegram_id"],
            defaults={
                "username": data.get("username") or "",
                "first_name": data.get("first_name") or "",
                "language_code": data.get("language_code") or "ru",
            },
        )
        NotificationSettings.objects.get_or_create(user=user)
        return Response(TelegramUserSerializer(user).data)


class ConnectView(APIView):
    permission_classes = [HasInternalToken]

    def post(self, request, telegram_id: int):
        user = get_user_or_404(telegram_id)
        if not user:
            return Response({"detail": "User not found"}, status=status.HTTP_404_NOT_FOUND)

        serializer = ConnectSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        api_key = serializer.validated_data["api_key"]

        client = IntervalsClient(api_key=api_key)
        try:
            athlete = client.get_athlete()
        except IntervalsAPIError as exc:
            return Response(
                {"detail": str(exc), "valid": False},
                status=status.HTTP_400_BAD_REQUEST,
            )

        athlete_id = str(athlete.get("id") or athlete.get("athlete_id") or "")
        creds, _ = IntervalsCredentials.objects.get_or_create(user=user)
        creds.set_api_key(api_key)
        creds.athlete_id = athlete_id
        creds.is_valid = True
        creds.last_validated_at = timezone.now()
        creds.last_error = ""
        creds.save()

        from apps.intervals.tasks import sync_user_calendar, sync_user_form

        try:
            sync_user_form.delay(user.id)
            sync_user_calendar.delay(user.id)
        except Exception:
            # Broker unavailable (e.g. local without Redis) — sync later via beat
            pass

        return Response(
            {
                "valid": True,
                "athlete_id": athlete_id,
                "athlete_name": athlete.get("name") or athlete.get("firstname") or "",
            }
        )

    def delete(self, request, telegram_id: int):
        user = get_user_or_404(telegram_id)
        if not user:
            return Response({"detail": "User not found"}, status=status.HTTP_404_NOT_FOUND)
        IntervalsCredentials.objects.filter(user=user).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class UserDetailView(APIView):
    permission_classes = [HasInternalToken]

    def get(self, request, telegram_id: int):
        user = get_user_or_404(telegram_id)
        if not user:
            return Response({"detail": "User not found"}, status=status.HTTP_404_NOT_FOUND)
        return Response(TelegramUserSerializer(user).data)


class SettingsView(APIView):
    permission_classes = [HasInternalToken]

    def get(self, request, telegram_id: int):
        user = get_user_or_404(telegram_id)
        if not user:
            return Response({"detail": "User not found"}, status=status.HTTP_404_NOT_FOUND)
        settings_obj, _ = NotificationSettings.objects.get_or_create(user=user)
        return Response(NotificationSettingsSerializer(settings_obj).data)

    def patch(self, request, telegram_id: int):
        user = get_user_or_404(telegram_id)
        if not user:
            return Response({"detail": "User not found"}, status=status.HTTP_404_NOT_FOUND)
        settings_obj, _ = NotificationSettings.objects.get_or_create(user=user)
        serializer = NotificationSettingsSerializer(
            settings_obj, data=request.data, partial=True
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(NotificationSettingsSerializer(settings_obj).data)


class AnalyzeView(APIView):
    """Force day/week AI analysis for a Telegram user (subscription required)."""

    permission_classes = [HasInternalToken]

    def post(self, request, telegram_id: int):
        from datetime import date
        from zoneinfo import ZoneInfo

        from apps.ai.analysis import resolve_period_dates
        from apps.notifications.tasks import send_period_analysis

        user = get_user_or_404(telegram_id)
        if not user:
            return Response({"detail": "User not found"}, status=status.HTTP_404_NOT_FOUND)
        if not hasattr(user, "credentials") or not user.credentials.is_valid:
            return Response(
                {"detail": "Intervals account not connected"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not user.has_active_subscription:
            return Response(
                {"detail": "Active subscription required for AI analysis"},
                status=status.HTTP_403_FORBIDDEN,
            )

        kind = (request.data.get("kind") or "day").strip().lower()
        if kind not in ("day", "week"):
            return Response(
                {"detail": "kind must be 'day' or 'week'"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            tz = ZoneInfo(str(user.timezone))
        except Exception:
            tz = ZoneInfo("Europe/Moscow")
        today = timezone.now().astimezone(tz).date()

        date_raw = request.data.get("date")
        if date_raw:
            try:
                today = date.fromisoformat(str(date_raw)[:10])
            except ValueError:
                return Response(
                    {"detail": "Invalid date, use YYYY-MM-DD"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        start_d, end_d = resolve_period_dates(kind, today=today)
        send_period_analysis.delay(
            user.id,
            kind,
            start_d.isoformat(),
            end_d.isoformat(),
            True,  # force
            False,  # skip_subscription_check
            False,  # resend_only
        )
        return Response(
            {
                "queued": True,
                "kind": kind,
                "period_start": start_d.isoformat(),
                "period_end": end_d.isoformat(),
            },
            status=status.HTTP_202_ACCEPTED,
        )
