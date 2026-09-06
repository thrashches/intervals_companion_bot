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
    sync_calendar,
    sync_form,
)
from apps.users.models import TelegramUser
from apps.users.permissions import HasInternalToken


class PlanView(APIView):
    permission_classes = [HasInternalToken]

    def get(self, request, telegram_id: int):
        try:
            user = TelegramUser.objects.select_related("credentials").get(
                telegram_id=telegram_id
            )
        except TelegramUser.DoesNotExist:
            return Response({"detail": "User not found"}, status=status.HTTP_404_NOT_FOUND)
        if not hasattr(user, "credentials") or not user.credentials.is_valid:
            return Response(
                {"detail": "Intervals account not connected"},
                status=status.HTTP_400_BAD_REQUEST,
            )
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
        try:
            user = TelegramUser.objects.select_related("credentials").get(
                telegram_id=telegram_id
            )
        except TelegramUser.DoesNotExist:
            return Response({"detail": "User not found"}, status=status.HTTP_404_NOT_FOUND)
        if not hasattr(user, "credentials") or not user.credentials.is_valid:
            return Response(
                {"detail": "Intervals account not connected"},
                status=status.HTTP_400_BAD_REQUEST,
            )
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
        try:
            user = TelegramUser.objects.select_related(
                "credentials", "athlete_snapshot"
            ).get(telegram_id=telegram_id)
        except TelegramUser.DoesNotExist:
            return Response({"detail": "User not found"}, status=status.HTTP_404_NOT_FOUND)
        if not hasattr(user, "credentials") or not user.credentials.is_valid:
            return Response(
                {"detail": "Intervals account not connected"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        refresh = request.query_params.get("refresh") == "1"
        if refresh or not hasattr(user, "athlete_snapshot"):
            try:
                sync_form(user)
                user.refresh_from_db()
            except Exception as exc:
                return Response({"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
        return Response(form_payload(user))
