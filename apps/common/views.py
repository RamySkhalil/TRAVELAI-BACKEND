from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db.models import Count
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


ROLE_DESCRIPTIONS = {
    "SuperAdmin": "Full TravelOps access. Bypasses access scopes and manages all administration.",
    "Admin": "Manages master data and normal users. Cannot grant SuperAdmin.",
    "HR": "Creates and follows travel requests, employee travel data, permits, and HR approvals.",
    "BookingOfficer": "Works the booking desk, confirms ticket extractions, and updates booking progress.",
    "BookingManager": "Oversees booking work queues, supplier invoice flow, and billing confirmations.",
    "Finance": "Handles supplier invoices, TBCN finance flow, payment status, and finance reporting.",
    "Auditor": "Read-only oversight across travel cases, invoices, billing confirmations, and audit logs.",
}

ROLE_BUSINESS_AREAS = {
    "SuperAdmin": "System administration, user management, all operational areas",
    "Admin": "Administration, master data, user management",
    "HR": "Travel requests, employees, permits, HR approvals",
    "BookingOfficer": "Booking desk, ticket extraction, ticket versions",
    "BookingManager": "Booking oversight, supplier invoices, billing confirmations",
    "Finance": "Supplier invoices, TBCN, payments, finance reports",
    "Auditor": "Audit logs and read-only operational oversight",
}


class CurrentUserSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    username = serializers.CharField()
    email = serializers.EmailField(allow_blank=True)
    first_name = serializers.CharField(allow_blank=True)
    last_name = serializers.CharField(allow_blank=True)
    is_staff = serializers.BooleanField()
    is_superuser = serializers.BooleanField()
    is_super_admin = serializers.BooleanField()
    is_travelops_admin = serializers.BooleanField()
    groups = serializers.ListField(child=serializers.CharField())
    permissions = serializers.ListField(child=serializers.CharField())


class AdminSettingsSystemSerializer(serializers.Serializer):
    health = serializers.CharField()
    readiness = serializers.CharField()
    database = serializers.CharField()
    database_connected = serializers.BooleanField()
    database_url_enabled = serializers.BooleanField()
    r2_enabled = serializers.BooleanField()
    storage_backend = serializers.CharField()
    openai_configured = serializers.BooleanField()
    debug = serializers.BooleanField()
    allowed_hosts_configured = serializers.BooleanField()
    cors_configured = serializers.BooleanField()
    csrf_configured = serializers.BooleanField()


class AdminSettingsCurrentUserSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    username = serializers.CharField()
    email = serializers.EmailField(allow_blank=True)
    is_staff = serializers.BooleanField()
    is_superuser = serializers.BooleanField()
    is_super_admin = serializers.BooleanField()
    is_travelops_admin = serializers.BooleanField()
    groups = serializers.ListField(child=serializers.CharField())


class AdminSettingsRoleCountSerializer(serializers.Serializer):
    role = serializers.CharField()
    count = serializers.IntegerField()


class AdminSettingsUsersSerializer(serializers.Serializer):
    total = serializers.IntegerField()
    active = serializers.IntegerField()
    inactive = serializers.IntegerField()
    by_role = AdminSettingsRoleCountSerializer(many=True)
    manage_users_url = serializers.CharField()


class AdminSettingsRoleSerializer(serializers.Serializer):
    name = serializers.CharField()
    description = serializers.CharField()
    allowed_business_area = serializers.CharField()
    user_count = serializers.IntegerField()


class AdminSettingsMasterDataSerializer(serializers.Serializer):
    countries = serializers.IntegerField()
    projects = serializers.IntegerField()
    departments = serializers.IntegerField()
    suppliers = serializers.IntegerField()
    routes = serializers.IntegerField()
    employees = serializers.IntegerField()


class AdminSettingsAccessScopeTypeSerializer(serializers.Serializer):
    scope_type = serializers.CharField()
    count = serializers.IntegerField()


class AdminSettingsAccessScopesSerializer(serializers.Serializer):
    enabled = serializers.BooleanField()
    message = serializers.CharField()
    total_active = serializers.IntegerField()
    users_with_scopes = serializers.IntegerField()
    by_type = AdminSettingsAccessScopeTypeSerializer(many=True)
    management_url = serializers.CharField(allow_blank=True)
    planned_model = serializers.ListField(child=serializers.CharField())


class AdminSettingsAuditActionSerializer(serializers.Serializer):
    action = serializers.CharField()
    entity_type = serializers.CharField()
    created_at = serializers.DateTimeField()
    username = serializers.CharField(allow_blank=True, allow_null=True)


class AdminSettingsAuditSerializer(serializers.Serializer):
    total_count = serializers.IntegerField()
    recent_admin_security_count = serializers.IntegerField()
    recent_admin_security_actions = AdminSettingsAuditActionSerializer(many=True)
    message = serializers.CharField()


class AdminSettingsSecuritySerializer(serializers.Serializer):
    secrets_exposed = serializers.BooleanField()
    env_not_exposed = serializers.BooleanField()
    uses_supabase_auth = serializers.BooleanField()
    uses_supabase_storage = serializers.BooleanField()
    frontend_uses_django_api = serializers.BooleanField()
    r2_enabled = serializers.BooleanField()
    openai_configured = serializers.BooleanField()
    debug = serializers.BooleanField()
    allowed_hosts_configured = serializers.BooleanField()
    cors_configured = serializers.BooleanField()
    csrf_configured = serializers.BooleanField()


class AdminSettingsSummarySerializer(serializers.Serializer):
    system = AdminSettingsSystemSerializer()
    current_user = AdminSettingsCurrentUserSerializer()
    users = AdminSettingsUsersSerializer()
    roles = AdminSettingsRoleSerializer(many=True)
    master_data = AdminSettingsMasterDataSerializer()
    access_scopes = AdminSettingsAccessScopesSerializer()
    audit = AdminSettingsAuditSerializer()
    security = AdminSettingsSecuritySerializer()


@extend_schema(responses=CurrentUserSerializer)
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def current_user(request):
    from apps.access_control.permissions import is_admin, is_super_admin

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
            "is_super_admin": is_super_admin(user),
            "is_travelops_admin": is_admin(user),
            "groups": list(user.groups.order_by("name").values_list("name", flat=True)),
            "permissions": sorted(user.get_all_permissions()),
        }
    )


def _role_counts(role_names: tuple[str, ...]) -> dict[str, int]:
    counts = {name: 0 for name in role_names}
    annotated = Group.objects.filter(name__in=role_names).annotate(user_count=Count("user")).values("name", "user_count")
    for row in annotated:
        counts[row["name"]] = row["user_count"]
    return counts


@extend_schema(responses=AdminSettingsSummarySerializer)
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def admin_settings_summary(request):
    from apps.access_control.models import ScopeType, UserAccessScope
    from apps.access_control.permissions import ROLE_GROUPS, IsTravelOpsAdmin, get_user_roles, is_admin, is_super_admin
    from apps.audit_logs.models import AuditLog
    from apps.master_data.models import Country, Department, Employee, Project, Route, Supplier

    permission = IsTravelOpsAdmin()
    if not permission.has_permission(request, admin_settings_summary):
        from rest_framework.exceptions import PermissionDenied

        raise PermissionDenied(permission.message)

    user_model = get_user_model()
    database_ok = check_database_connection()
    environment = collect_environment_report(check_database=False)
    storage = storage_status()
    role_counts = _role_counts(ROLE_GROUPS)

    by_role = [{"role": role, "count": role_counts[role]} for role in ROLE_GROUPS]
    active_scopes = UserAccessScope.objects.filter(is_active=True)
    scope_counts = {
        row["scope_type"]: row["count"]
        for row in active_scopes.values("scope_type").annotate(count=Count("id")).order_by("scope_type")
    }
    recent_admin_actions = list(
        AuditLog.objects.select_related("user")
        .filter(action__in=["User Created", "User Updated", "User Disabled", "Role Assigned", "Scope Created", "Scope Updated", "Scope Deactivated"])
        .order_by("-created_at")[:5]
    )

    payload = {
        "system": {
            "health": "ok",
            "readiness": "ok" if database_ok else "degraded",
            "database": "ok" if database_ok else "error",
            "database_connected": database_ok,
            "database_url_enabled": bool(environment["database_url_enabled"]),
            "r2_enabled": bool(storage["r2_enabled"]),
            "storage_backend": storage["backend"],
            "openai_configured": bool(environment["openai_configured"]),
            "debug": bool(environment["debug"]),
            "allowed_hosts_configured": bool(environment["allowed_hosts_configured"]),
            "cors_configured": bool(environment["cors_configured"]),
            "csrf_configured": bool(environment["csrf_trusted_origins_count"]),
        },
        "current_user": {
            "id": request.user.id,
            "username": request.user.get_username(),
            "email": request.user.email,
            "is_staff": request.user.is_staff,
            "is_superuser": request.user.is_superuser,
            "is_super_admin": is_super_admin(request.user),
            "is_travelops_admin": is_admin(request.user),
            "groups": get_user_roles(request.user),
        },
        "users": {
            "total": user_model.objects.count(),
            "active": user_model.objects.filter(is_active=True).count(),
            "inactive": user_model.objects.filter(is_active=False).count(),
            "by_role": by_role,
            "manage_users_url": "/admin/users",
        },
        "roles": [
            {
                "name": role,
                "description": ROLE_DESCRIPTIONS.get(role, ""),
                "allowed_business_area": ROLE_BUSINESS_AREAS.get(role, ""),
                "user_count": role_counts[role],
            }
            for role in ROLE_GROUPS
        ],
        "master_data": {
            "countries": Country.objects.count(),
            "projects": Project.objects.count(),
            "departments": Department.objects.count(),
            "suppliers": Supplier.objects.count(),
            "routes": Route.objects.count(),
            "employees": Employee.objects.count(),
        },
        "access_scopes": {
            "enabled": True,
            "message": "Access scopes are active and enforced by backend query filters where supported.",
            "total_active": active_scopes.count(),
            "users_with_scopes": active_scopes.values("user_id").distinct().count(),
            "by_type": [{"scope_type": scope_type, "count": scope_counts.get(scope_type, 0)} for scope_type in ScopeType.values],
            "management_url": "/admin/access-scopes",
            "planned_model": ["Global", "Country", "Project", "Department", "Assigned-to-me", "Created-by-me"],
        },
        "audit": {
            "total_count": AuditLog.objects.count(),
            "recent_admin_security_count": len(recent_admin_actions),
            "recent_admin_security_actions": [
                {
                    "action": log.action,
                    "entity_type": log.entity_type,
                    "created_at": log.created_at,
                    "username": log.user.get_username() if log.user_id else None,
                }
                for log in recent_admin_actions
            ],
            "message": "Recent action summary only. Full audit detail requires the audit log endpoint and permissions.",
        },
        "security": {
            "secrets_exposed": False,
            "env_not_exposed": True,
            "uses_supabase_auth": False,
            "uses_supabase_storage": False,
            "frontend_uses_django_api": True,
            "r2_enabled": bool(storage["r2_enabled"]),
            "openai_configured": bool(environment["openai_configured"]),
            "debug": bool(environment["debug"]),
            "allowed_hosts_configured": bool(environment["allowed_hosts_configured"]),
            "cors_configured": bool(environment["cors_configured"]),
            "csrf_configured": bool(environment["csrf_trusted_origins_count"]),
        },
    }
    return Response(payload)


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
