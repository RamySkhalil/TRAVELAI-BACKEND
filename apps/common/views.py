from django.conf import settings
from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAdminUser, IsAuthenticated
from rest_framework.response import Response

from apps.common.services.env_check import (
    check_database_connection,
    collect_environment_report,
    storage_status,
)


class CurrentUserSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    username = serializers.CharField()
    email = serializers.EmailField(allow_blank=True)
    first_name = serializers.CharField(allow_blank=True)
    last_name = serializers.CharField(allow_blank=True)
    is_staff = serializers.BooleanField()
    is_superuser = serializers.BooleanField()
    groups = serializers.ListField(child=serializers.CharField())
    permissions = serializers.ListField(child=serializers.CharField())


@extend_schema(responses=CurrentUserSerializer)
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def current_user(request):
    user = request.user

    return Response(
        {
            "id": user.id,
            "username": user.get_username(),
            "email": user.email,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "is_staff": user.is_staff,
            "is_superuser": user.is_superuser,
            "groups": list(user.groups.order_by("name").values_list("name", flat=True)),
            "permissions": sorted(user.get_all_permissions()),
        }
    )


class HealthResponseSerializer(serializers.Serializer):
    status = serializers.CharField()
    service = serializers.CharField()
    version = serializers.CharField()


class ReadinessStorageSerializer(serializers.Serializer):
    r2_enabled = serializers.BooleanField()
    backend = serializers.CharField()
    r2_missing_env_names = serializers.ListField(child=serializers.CharField())


class ReadinessResponseSerializer(serializers.Serializer):
    status = serializers.CharField()
    database = serializers.CharField()
    storage = ReadinessStorageSerializer()
    database_url_enabled = serializers.BooleanField()
    environment = serializers.DictField()


def _app_version() -> str:
    return str(settings.SPECTACULAR_SETTINGS.get("VERSION", "unknown"))


@extend_schema(responses=HealthResponseSerializer)
@api_view(["GET"])
@permission_classes([AllowAny])
def health(request):
    """Lightweight liveness probe. Safe to expose without authentication."""
    return Response(
        {
            "status": "ok",
            "service": "travelops-backend",
            "version": _app_version(),
        }
    )


@extend_schema(responses=ReadinessResponseSerializer)
@api_view(["GET"])
@permission_classes([IsAdminUser])
def readiness(request):
    """Readiness probe for staff/admin users.

    Reports dependency status (database, storage configuration) and a redacted
    environment report. Never returns secret values.
    """
    database_ok = check_database_connection()
    storage = storage_status()
    overall_ok = database_ok

    return Response(
        {
            "status": "ok" if overall_ok else "degraded",
            "database": "ok" if database_ok else "error",
            "storage": {
                "r2_enabled": storage["r2_enabled"],
                "backend": storage["backend"],
                "r2_missing_env_names": storage["r2_missing_env_names"],
            },
            "database_url_enabled": bool(getattr(settings, "DATABASE_URL_ENABLED", False)),
            "environment": collect_environment_report(check_database=False),
        }
    )
