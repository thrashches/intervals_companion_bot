from rest_framework.permissions import BasePermission
from django.conf import settings


class HasInternalToken(BasePermission):
    def has_permission(self, request, view) -> bool:
        expected = settings.INTERNAL_API_TOKEN
        if not expected:
            return False
        return request.headers.get("X-Internal-Token") == expected
