"""Reusable access-control helpers and DRF permissions.

These helpers centralize how TravelOps interprets Django users, groups, and
in-app access scopes. They are intentionally conservative: a user with no
configured scopes keeps the role-based visibility they had before Phase 19, so
existing behavior is preserved.
"""
from __future__ import annotations

from django.db.models import Q
from rest_framework.permissions import SAFE_METHODS, BasePermission

from .models import ScopeType, UserAccessScope

SUPER_ADMIN_GROUP = "SuperAdmin"
ADMIN_GROUP = "Admin"
AUDITOR_GROUP = "Auditor"

# Canonical role/group names managed by the in-app administration layer.
ROLE_GROUPS = (
    "SuperAdmin",
    "Admin",
    "HR",
    "BookingOfficer",
    "BookingManager",
    "Finance",
    "Auditor",
)


def get_user_roles(user) -> list[str]:
    """Return the group/role names assigned to a user (empty for anonymous)."""
    if not user or not user.is_authenticated:
        return []
    return list(user.groups.order_by("name").values_list("name", flat=True))


def is_super_admin(user) -> bool:
    """A Django superuser is always a Super Admin; profile flag and group also grant it."""
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    profile = getattr(user, "travelops_profile", None)
    if profile is not None and profile.is_super_admin:
        return True
    return user.groups.filter(name=SUPER_ADMIN_GROUP).exists()


def is_admin(user) -> bool:
    """Admins manage users/master data. Super Admins and Django staff are included."""
    if not user or not user.is_authenticated:
        return False
    if is_super_admin(user) or user.is_staff:
        return True
    return user.groups.filter(name=ADMIN_GROUP).exists()


def is_auditor(user) -> bool:
    if not user or not user.is_authenticated:
        return False
    return user.groups.filter(name=AUDITOR_GROUP).exists()


def get_user_scopes(user):
    """Active access scopes for a user as a queryset (empty for anonymous)."""
    if not user or not user.is_authenticated:
        return UserAccessScope.objects.none()
    return UserAccessScope.objects.filter(user=user, is_active=True)


# Field path templates per supported model. ``ownership`` paths always remain
# visible so assigned-to-me and created-by-me records are never hidden.
_SCOPE_FIELD_MAP: dict[str, dict[str, str]] = {
    "travel_case": {
        "country": "country_id__in",
        "project": "project_id__in",
        "department": "department_id__in",
    },
    "supplier_invoice": {
        "country": "lines__travel_case__country_id__in",
        "project": "lines__travel_case__project_id__in",
        "department": "lines__travel_case__department_id__in",
    },
    "tbcn": {
        "country": "supplier_invoice__lines__travel_case__country_id__in",
        "project": "supplier_invoice__lines__travel_case__project_id__in",
        "department": "supplier_invoice__lines__travel_case__department_id__in",
    },
}

# Ownership fields that keep records visible regardless of scope boundaries.
_OWNERSHIP_FIELDS: dict[str, tuple[str, ...]] = {
    "travel_case": ("created_by", "assigned_to"),
    "supplier_invoice": ("created_by",),
    "tbcn": ("generated_by",),
}


def apply_scope_filter(queryset, user, model_type: str):
    """Restrict ``queryset`` to records visible under the user's access scopes.

    Rules:
    - Super Admins bypass scope filtering entirely.
    - A user with no active view scopes keeps role-based visibility (no change).
    - A GLOBAL view scope means no scope restriction.
    - Otherwise visibility is the union of the user's country/project/department
      scopes, plus any record they created or are assigned to.
    """
    if is_super_admin(user):
        return queryset

    field_map = _SCOPE_FIELD_MAP.get(model_type)
    if field_map is None:
        return queryset

    scopes = [scope for scope in get_user_scopes(user) if scope.can_view]
    if not scopes:
        return queryset
    if any(scope.scope_type == ScopeType.GLOBAL for scope in scopes):
        return queryset

    country_ids = {s.country_id for s in scopes if s.scope_type == ScopeType.COUNTRY and s.country_id}
    project_ids = {s.project_id for s in scopes if s.scope_type == ScopeType.PROJECT and s.project_id}
    department_ids = {s.department_id for s in scopes if s.scope_type == ScopeType.DEPARTMENT and s.department_id}

    scope_query = Q()
    if country_ids:
        scope_query |= Q(**{field_map["country"]: country_ids})
    if project_ids:
        scope_query |= Q(**{field_map["project"]: project_ids})
    if department_ids:
        scope_query |= Q(**{field_map["department"]: department_ids})

    for ownership_field in _OWNERSHIP_FIELDS.get(model_type, ()):
        scope_query |= Q(**{ownership_field: user})

    if not scope_query:
        return queryset

    return queryset.filter(scope_query).distinct()


class IsTravelOpsAdmin(BasePermission):
    """Allow Super Admins and Admins to manage the administration layer."""

    message = "Administration access is limited to Super Admin and Admin users."

    def has_permission(self, request, view):
        return is_admin(request.user)
