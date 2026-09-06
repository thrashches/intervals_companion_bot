from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/v1/bot/", include("apps.users.urls")),
    path("api/v1/bot/", include("apps.intervals.urls")),
    path("api/v1/health/", include("apps.users.health_urls")),
]
