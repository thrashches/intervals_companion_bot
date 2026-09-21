from pathlib import Path

from django.conf import settings
from django.http import FileResponse, Http404
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.intervals.services import (
    form_payload,
    plan_payload,
    recent_reports_payload,
    sync_activities_for_range,
    sync_calendar,
    sync_form,
    zones_payload,
)
from apps.users.models import TelegramUser
from apps.users.permissions import HasInternalToken


def _user_or_error(telegram_id: int):
    try:
        user = TelegramUser.objects.select_related(
            "credentials", "athlete_snapshot"
        ).get(telegram_id=telegram_id)
    except TelegramUser.DoesNotExist:
        return None, Response({"detail": "User not found"}, status=status.HTTP_404_NOT_FOUND)
    if not hasattr(user, "credentials") or not user.credentials.is_valid:
        return None, Response(
            {"detail": "Intervals account not connected"},
            status=status.HTTP_400_BAD_REQUEST,
        )
    return user, None


class PlanView(APIView):
    permission_classes = [HasInternalToken]

    def get(self, request, telegram_id: int):
        user, err = _user_or_error(telegram_id)
        if err:
            return err
        refresh = request.query_params.get("refresh") == "1"
        if refresh:
            try:
                sync_calendar(user)
            except Exception as exc:
                return Response({"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
        return Response(plan_payload(user, with_charts=True))


class RecentReportsView(APIView):
    permission_classes = [HasInternalToken]

    def get(self, request, telegram_id: int):
        user, err = _user_or_error(telegram_id)
        if err:
            return err
        try:
            limit = int(request.query_params.get("limit") or 5)
        except (TypeError, ValueError):
            limit = 5
        return Response(recent_reports_payload(user, limit=limit))


class MediaFileView(APIView):
    """Serve a file from MEDIA_ROOT for the bot (internal token required)."""

    permission_classes = [HasInternalToken]

    def get(self, request, rel_path: str):
        # Prevent path traversal
        rel = Path(rel_path)
        if ".." in rel.parts:
            raise Http404()
        media_root = Path(settings.MEDIA_ROOT).resolve()
        full = (media_root / rel).resolve()
        if not str(full).startswith(str(media_root)) or not full.is_file():
            raise Http404()
        return FileResponse(full.open("rb"), content_type="image/png")


class FormView(APIView):
    permission_classes = [HasInternalToken]

    def get(self, request, telegram_id: int):
        user, err = _user_or_error(telegram_id)
        if err:
            return err
        refresh = request.query_params.get("refresh") == "1"
        if refresh or not hasattr(user, "athlete_snapshot"):
            try:
                sync_form(user)
                user.refresh_from_db()
            except Exception as exc:
                return Response({"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
        with_chart = refresh or request.query_params.get("chart") == "1"
        return Response(form_payload(user, with_chart=with_chart))


class ZonesView(APIView):
    permission_classes = [HasInternalToken]

    def get(self, request, telegram_id: int):
        user, err = _user_or_error(telegram_id)
        if err:
            return err
        refresh = request.query_params.get("refresh") == "1"
        if refresh:
            try:
                sync_calendar(user, past_days=7, future_days=1)
            except Exception as exc:
                return Response({"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
            try:
                sync_activities_for_range(user, lookback_days=7)
            except Exception as exc:
                return Response({"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
        return Response(zones_payload(user))
