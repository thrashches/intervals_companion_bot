from django.urls import path

from apps.users.views import (
    AnalyzeView,
    ConnectView,
    SettingsView,
    UpsertUserView,
    UserDetailView,
)

urlpatterns = [
    path("users/upsert", UpsertUserView.as_view(), name="bot-users-upsert"),
    path("users/<int:telegram_id>", UserDetailView.as_view(), name="bot-users-detail"),
    path("users/<int:telegram_id>/connect", ConnectView.as_view(), name="bot-users-connect"),
    path("users/<int:telegram_id>/settings", SettingsView.as_view(), name="bot-users-settings"),
    path("users/<int:telegram_id>/analyze", AnalyzeView.as_view(), name="bot-users-analyze"),
]
