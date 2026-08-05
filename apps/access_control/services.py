"""Business logic for the in-app administration layer.

All mutations route through these helpers so audit logging stays consistent and
Super Admin protection is enforced in one place. Passwords are never logged.
"""
from __future__ import annotations

import secrets

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import transaction
from rest_framework.exceptions import PermissionDenied

from apps.audit_logs.services import create_audit_log

from .models import TravelOpsUserProfile, UserAccessScope
from .permissions import ADMIN_GROUP, ROLE_GROUPS, SUPER_ADMIN_GROUP, get_user_roles, is_super_admin

User = get_user_model()

ENTITY_USER = "User"
ENTITY_SCOPE = "UserAccessScope"

ACTION_USER_CREATED = "User Created"
ACTION_USER_UPDATED = "User Updated"
ACTION_USER_DISABLED = "User Disabled"
ACTION_USER_ACTIVATED = "User Activated"
ACTION_PASSWORD_RESET = "Password Reset"
ACTION_GENERATED_PASSWORD_RESET = "Generated Password Reset"
ACTION_ROLE_ASSIGNED = "Role Assigned"
ACTION_ROLE_REMOVED = "Role Removed"
ACTION_SCOPE_CREATED = "Scope Created"
ACTION_SCOPE_UPDATED = "Scope Updated"
ACTION_SCOPE_DEACTIVATED = "Scope Deactivated"


def generate_temporary_password() -> str:
    """Return a strong one-time password for invitation/demo flows."""
    return secrets.token_urlsafe(12)


def get_or_create_profile(user) -> TravelOpsUserProfile:
    profile, _ = TravelOpsUserProfile.objects.get_or_create(
        user=user,
        defaults={"display_name": f"{user.first_name} {user.last_name}".strip() or user.username},
    )
    return profile


def _ensure_can_grant_super_admin(roles, acting_user) -> None:
    if SUPER_ADMIN_GROUP in set(roles) and not is_super_admin(acting_user):
        raise PermissionDenied("Only a Super Admin can grant the SuperAdmin role.")


def _ensure_can_manage_target(user, acting_user) -> None:
    if is_super_admin(user) and not is_super_admin(acting_user):
        raise PermissionDenied("Only a Super Admin can manage another Super Admin.")


def _ensure_can_change_staff(acting_user) -> None:
    if not is_super_admin(acting_user):
        raise PermissionDenied("Only a Super Admin can change staff status.")


def _set_roles(user, roles) -> None:
    groups = list(Group.objects.filter(name__in=roles))
    user.groups.set(groups)


@transaction.atomic
def create_managed_user(*, validated_data: dict, acting_user, ip_address: str | None = None):
    """Create a user, profile, and roles. Returns (user, generated_password|None)."""
    roles = validated_data.get("roles") or []
    _ensure_can_grant_super_admin(roles, acting_user)

    generated_password: str | None = None
    raw_password = validated_data.get("password") or ""
    if validated_data.get("generate_password"):
        raw_password = generate_temporary_password()
        generated_password = raw_password

    user = User(
        username=validated_data["username"],
        email=validated_data.get("email", ""),
        first_name=validated_data.get("first_name", ""),
        last_name=validated_data.get("last_name", ""),
    )
    user.set_password(raw_password)
    user.save()

    TravelOpsUserProfile.objects.create(
        user=user,
        display_name=validated_data.get("display_name") or f"{user.first_name} {user.last_name}".strip() or user.username,
        job_title=validated_data.get("job_title", ""),
        notes=validated_data.get("notes", ""),
    )

    if roles:
        Group.objects.bulk_create([Group(name=name) for name in ROLE_GROUPS], ignore_conflicts=True)
        _set_roles(user, roles)

    create_audit_log(
        user=acting_user,
        action=ACTION_USER_CREATED,
        entity_type=ENTITY_USER,
        entity_id=user.id,
        new_value={"username": user.username, "roles": roles, "password_generated": generated_password is not None},
        metadata={"email": user.email},
        ip_address=ip_address,
    )
    return user, generated_password


@transaction.atomic
def update_managed_user(*, user, validated_data: dict, acting_user, ip_address: str | None = None):
    _ensure_can_manage_target(user, acting_user)
    if "is_staff" in validated_data:
        _ensure_can_change_staff(acting_user)

    user_fields = ("email", "first_name", "last_name", "is_staff")
    changed: dict[str, object] = {}
    for field in user_fields:
        if field in validated_data:
            setattr(user, field, validated_data[field])
            changed[field] = validated_data[field]
    if changed:
        user.save(update_fields=list(changed.keys()))

    profile = get_or_create_profile(user)
    profile_fields = ("job_title", "display_name", "notes", "is_travelops_active")
    profile_changed = {}
    for field in profile_fields:
        if field in validated_data:
            setattr(profile, field, validated_data[field])
            profile_changed[field] = validated_data[field]
    if profile_changed:
        profile.save(update_fields=list(profile_changed.keys()) + ["updated_at"])

    if changed or profile_changed:
        create_audit_log(
            user=acting_user,
            action=ACTION_USER_UPDATED,
            entity_type=ENTITY_USER,
            entity_id=user.id,
            new_value={**changed, **profile_changed},
            ip_address=ip_address,
        )
    return user


@transaction.atomic
def disable_managed_user(*, user, acting_user, ip_address: str | None = None):
    _ensure_can_manage_target(user, acting_user)
    if user.id == acting_user.id:
        raise PermissionDenied("You cannot disable your own account.")
    user.is_active = False
    user.save(update_fields=["is_active"])
    profile = get_or_create_profile(user)
    if profile.is_travelops_active:
        profile.is_travelops_active = False
        profile.save(update_fields=["is_travelops_active", "updated_at"])
    create_audit_log(
        user=acting_user,
        action=ACTION_USER_DISABLED,
        entity_type=ENTITY_USER,
        entity_id=user.id,
        new_value={"is_active": False},
        ip_address=ip_address,
    )
    return user


@transaction.atomic
def activate_managed_user(*, user, acting_user, ip_address: str | None = None):
    _ensure_can_manage_target(user, acting_user)
    user.is_active = True
    user.save(update_fields=["is_active"])
    profile = get_or_create_profile(user)
    if not profile.is_travelops_active:
        profile.is_travelops_active = True
        profile.save(update_fields=["is_travelops_active", "updated_at"])
    create_audit_log(
        user=acting_user,
        action=ACTION_USER_ACTIVATED,
        entity_type=ENTITY_USER,
        entity_id=user.id,
        new_value={"is_active": True},
        ip_address=ip_address,
    )
    return user


@transaction.atomic
def reset_managed_user_password(*, user, raw_password: str, acting_user, generated: bool = False, ip_address: str | None = None):
    _ensure_can_manage_target(user, acting_user)
    user.set_password(raw_password)
    user.save(update_fields=["password"])
    create_audit_log(
        user=acting_user,
        action=ACTION_GENERATED_PASSWORD_RESET if generated else ACTION_PASSWORD_RESET,
        entity_type=ENTITY_USER,
        entity_id=user.id,
        new_value={"password_reset": True, "generated": generated},
        ip_address=ip_address,
    )
    return user


@transaction.atomic
def assign_roles(*, user, roles, acting_user, ip_address: str | None = None):
    _ensure_can_manage_target(user, acting_user)
    current = set(get_user_roles(user))
    requested = set(roles)
    added = sorted(requested - current)
    removed = sorted(current - requested)

    # Only a Super Admin may add or remove the SuperAdmin role.
    if (SUPER_ADMIN_GROUP in added or SUPER_ADMIN_GROUP in removed) and not is_super_admin(acting_user):
        raise PermissionDenied("Only a Super Admin can change the SuperAdmin role.")

    if (
        user.id == acting_user.id
        and {ADMIN_GROUP, SUPER_ADMIN_GROUP}.intersection(current)
        and not {ADMIN_GROUP, SUPER_ADMIN_GROUP}.intersection(requested)
        and not user.is_staff
        and not user.is_superuser
        and not getattr(getattr(user, "travelops_profile", None), "is_super_admin", False)
    ):
        raise PermissionDenied("You cannot remove your own last admin access.")

    Group.objects.bulk_create([Group(name=name) for name in ROLE_GROUPS], ignore_conflicts=True)
    _set_roles(user, roles)

    if added:
        create_audit_log(
            user=acting_user,
            action=ACTION_ROLE_ASSIGNED,
            entity_type=ENTITY_USER,
            entity_id=user.id,
            new_value={"added": added},
            ip_address=ip_address,
        )
    if removed:
        create_audit_log(
            user=acting_user,
            action=ACTION_ROLE_REMOVED,
            entity_type=ENTITY_USER,
            entity_id=user.id,
            old_value={"removed": removed},
            ip_address=ip_address,
        )
    return user


def _scope_snapshot(scope: UserAccessScope) -> dict:
    return {
        "scope_type": scope.scope_type,
        "country": scope.country_id,
        "project": scope.project_id,
        "department": scope.department_id,
        "can_view": scope.can_view,
        "can_create": scope.can_create,
        "can_approve": scope.can_approve,
        "can_finance": scope.can_finance,
        "is_active": scope.is_active,
    }


def log_scope_created(*, scope: UserAccessScope, acting_user, ip_address: str | None = None) -> None:
    create_audit_log(
        user=acting_user,
        action=ACTION_SCOPE_CREATED,
        entity_type=ENTITY_SCOPE,
        entity_id=scope.id,
        new_value=_scope_snapshot(scope),
        metadata={"target_user": scope.user_id},
        ip_address=ip_address,
    )


def log_scope_updated(*, scope: UserAccessScope, old_value: dict, acting_user, ip_address: str | None = None) -> None:
    create_audit_log(
        user=acting_user,
        action=ACTION_SCOPE_UPDATED,
        entity_type=ENTITY_SCOPE,
        entity_id=scope.id,
        old_value=old_value,
        new_value=_scope_snapshot(scope),
        metadata={"target_user": scope.user_id},
        ip_address=ip_address,
    )


@transaction.atomic
def deactivate_scope(*, scope: UserAccessScope, acting_user, ip_address: str | None = None) -> UserAccessScope:
    old_value = _scope_snapshot(scope)
    if scope.is_active:
        scope.is_active = False
        scope.save(update_fields=["is_active", "updated_at"])
    create_audit_log(
        user=acting_user,
        action=ACTION_SCOPE_DEACTIVATED,
        entity_type=ENTITY_SCOPE,
        entity_id=scope.id,
        old_value=old_value,
        new_value=_scope_snapshot(scope),
        metadata={"target_user": scope.user_id},
        ip_address=ip_address,
    )
    return scope
