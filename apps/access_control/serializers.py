from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from .models import ScopeType, TravelOpsUserProfile, UserAccessScope
from .permissions import ROLE_GROUPS, get_user_roles, is_super_admin
from apps.audit_logs.models import AuditLog

User = get_user_model()


class AccessScopeSerializer(serializers.ModelSerializer):
    country_name = serializers.CharField(source="country.name", read_only=True, default=None)
    project_name = serializers.CharField(source="project.name", read_only=True, default=None)
    department_name = serializers.CharField(source="department.name", read_only=True, default=None)
    username = serializers.CharField(source="user.username", read_only=True)

    class Meta:
        model = UserAccessScope
        fields = (
            "id",
            "user",
            "username",
            "scope_type",
            "country",
            "country_name",
            "project",
            "project_name",
            "department",
            "department_name",
            "can_view",
            "can_create",
            "can_approve",
            "can_finance",
            "is_active",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "created_at", "updated_at")

    def validate(self, attrs):
        scope_type = attrs.get("scope_type", getattr(self.instance, "scope_type", None))
        country = attrs.get("country", getattr(self.instance, "country", None))
        project = attrs.get("project", getattr(self.instance, "project", None))
        department = attrs.get("department", getattr(self.instance, "department", None))

        required = {
            ScopeType.COUNTRY: ("country", country),
            ScopeType.PROJECT: ("project", project),
            ScopeType.DEPARTMENT: ("department", department),
        }
        if scope_type in required:
            field_name, value = required[scope_type]
            if value is None:
                raise serializers.ValidationError({field_name: f"A {field_name} is required for a {scope_type} scope."})
        if scope_type == ScopeType.GLOBAL and (country or project or department):
            raise serializers.ValidationError("A GLOBAL scope must not target a country, project, or department.")
        return attrs


class ProfileSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = TravelOpsUserProfile
        fields = ("display_name", "job_title", "is_travelops_active", "is_super_admin", "notes")


class ManagedUserListSerializer(serializers.ModelSerializer):
    roles = serializers.SerializerMethodField()
    name = serializers.SerializerMethodField()
    is_super_admin = serializers.SerializerMethodField()
    scopes_summary = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = (
            "id",
            "username",
            "name",
            "email",
            "roles",
            "is_active",
            "is_staff",
            "is_superuser",
            "is_super_admin",
            "scopes_summary",
            "date_joined",
            "last_login",
        )

    def get_roles(self, obj) -> list[str]:
        return get_user_roles(obj)

    def get_name(self, obj) -> str:
        full_name = f"{obj.first_name} {obj.last_name}".strip()
        return full_name or obj.username

    def get_is_super_admin(self, obj) -> bool:
        return is_super_admin(obj)

    def get_scopes_summary(self, obj) -> str:
        scopes = [scope for scope in obj.access_scopes.all() if scope.is_active]
        if not scopes:
            return "No scopes (role default)"
        if any(scope.scope_type == ScopeType.GLOBAL for scope in scopes):
            return "Global"
        counts: dict[str, int] = {}
        for scope in scopes:
            counts[scope.scope_type] = counts.get(scope.scope_type, 0) + 1
        return ", ".join(f"{label.title()} x{count}" for label, count in sorted(counts.items()))


class ManagedUserDetailSerializer(ManagedUserListSerializer):
    profile = serializers.SerializerMethodField()
    scopes = serializers.SerializerMethodField()
    security_summary = serializers.SerializerMethodField()

    class Meta(ManagedUserListSerializer.Meta):
        fields = ManagedUserListSerializer.Meta.fields + ("first_name", "last_name", "profile", "scopes", "security_summary")

    @extend_schema_field(ProfileSummarySerializer)
    def get_profile(self, obj):
        profile = getattr(obj, "travelops_profile", None)
        if profile is None:
            return None
        return ProfileSummarySerializer(profile).data

    @extend_schema_field(AccessScopeSerializer(many=True))
    def get_scopes(self, obj):
        return AccessScopeSerializer(obj.access_scopes.all(), many=True).data

    def get_security_summary(self, obj) -> dict:
        logs = AuditLog.objects.filter(entity_type="User", entity_id=str(obj.id)).select_related("user")
        reset_actions = ("Password Reset", "Generated Password Reset")
        return {
            "audit_endpoint_available": True,
            "recent_admin_actions": [
                {
                    "action": log.action,
                    "created_at": log.created_at,
                    "actor": log.user.get_username() if log.user else "System",
                }
                for log in logs.order_by("-created_at")[:5]
            ],
            "password_reset_count": logs.filter(action__in=reset_actions).count(),
            "role_change_count": logs.filter(action__in=("Role Assigned", "Role Removed")).count(),
        }


class CreateUserSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=150)
    email = serializers.EmailField(required=False, allow_blank=True, default="")
    first_name = serializers.CharField(required=False, allow_blank=True, default="")
    last_name = serializers.CharField(required=False, allow_blank=True, default="")
    job_title = serializers.CharField(required=False, allow_blank=True, default="")
    display_name = serializers.CharField(required=False, allow_blank=True, default="")
    notes = serializers.CharField(required=False, allow_blank=True, default="")
    password = serializers.CharField(required=False, allow_blank=True, write_only=True, default="")
    generate_password = serializers.BooleanField(required=False, default=False)
    roles = serializers.ListField(child=serializers.CharField(), required=False, default=list)

    def validate_username(self, value):
        value = value.strip()
        if User.objects.filter(username__iexact=value).exists():
            raise serializers.ValidationError("A user with that username already exists.")
        return value

    def validate_roles(self, value):
        invalid = sorted(set(value) - set(ROLE_GROUPS))
        if invalid:
            raise serializers.ValidationError(f"Unknown role(s): {', '.join(invalid)}.")
        return value

    def validate(self, attrs):
        password = attrs.get("password") or ""
        generate = attrs.get("generate_password")
        if not password and not generate:
            raise serializers.ValidationError(
                {"password": "Provide a temporary password or set generate_password to true."}
            )
        if password and generate:
            raise serializers.ValidationError(
                {"password": "Provide a password or request generation, not both."}
            )
        if password:
            try:
                validate_password(password)
            except DjangoValidationError as exc:
                raise serializers.ValidationError({"password": list(exc.messages)})
        return attrs


class UpdateUserSerializer(serializers.Serializer):
    email = serializers.EmailField(required=False, allow_blank=True)
    first_name = serializers.CharField(required=False, allow_blank=True)
    last_name = serializers.CharField(required=False, allow_blank=True)
    is_staff = serializers.BooleanField(required=False)
    job_title = serializers.CharField(required=False, allow_blank=True)
    display_name = serializers.CharField(required=False, allow_blank=True)
    notes = serializers.CharField(required=False, allow_blank=True)
    is_travelops_active = serializers.BooleanField(required=False)


class AssignRolesSerializer(serializers.Serializer):
    roles = serializers.ListField(child=serializers.CharField(), allow_empty=True)

    def validate_roles(self, value):
        invalid = sorted(set(value) - set(ROLE_GROUPS))
        if invalid:
            raise serializers.ValidationError(f"Unknown role(s): {', '.join(invalid)}.")
        return value


class ResetPasswordSerializer(serializers.Serializer):
    password = serializers.CharField(write_only=True)

    def validate_password(self, value):
        try:
            validate_password(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(list(exc.messages))
        return value


class AvailableRoleSerializer(serializers.Serializer):
    name = serializers.CharField()
    description = serializers.CharField()
