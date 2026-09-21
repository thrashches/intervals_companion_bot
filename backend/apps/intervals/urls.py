from django.urls import path

from apps.intervals.views import FormView, MediaFileView, PlanView, RecentReportsView, ZonesView

urlpatterns = [
    path("users/<int:telegram_id>/plan", PlanView.as_view(), name="bot-users-plan"),
    path("users/<int:telegram_id>/form", FormView.as_view(), name="bot-users-form"),
    path("users/<int:telegram_id>/zones", ZonesView.as_view(), name="bot-users-zones"),
    path(
        "users/<int:telegram_id>/reports/recent",
        RecentReportsView.as_view(),
        name="bot-users-reports-recent",
    ),
    path("media/<path:rel_path>", MediaFileView.as_view(), name="bot-media"),
]
