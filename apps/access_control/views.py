from django.contrib.auth import get_user_model
from drf_spectacular.utils import extend_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.response import Response

from .models import UserAccessScope
from .permissions import ROLE_GROUPS, IsTravelOpsAdmin
from .serializers import (
    AccessScopeSerializer,
    AssignRolesSerializer,
    AvailableRoleSerializer,
    CreateUserSerializer,
    ManagedUserDetailSerializer,
    ManagedUserListSerializer,
    ResetPasswordSerializer,
    UpdateUserSerializer,
)
from .services import (
    activate_managed_user,
    assign_roles,
    create_managed_user,
    deactivate_scope,
    disable_managed_user,
    generate_temporary_password,
    log_scope_created,
    log_scope_updated,
    reset_managed_user_password,
    update_managed_user,
    _scope_snapshot,
)

User = get_user_model()

ROLE_DESCRIPTIONS = {
    "SuperAdmin": "Full TravelOps access. Bypasses access scopes and manages all administration.",
    "Admin": "Manages master data and normal users. Cannot grant SuperAdmin.",
    "HR": "Submits travel cases, approves invoices, and manages permits.",
    "BookingOfficer": "Works the booking desk and confirms ticket extractions.",
    "BookingManager": "Oversees booking, invoices, and billing confirmations.",
    "Finance": "Handles supplier invoices, TBCN finance flow, and payments.",
    "Auditor": "Read-only oversight across the workflow.",
}


def _client_ip(request) -> str | None:
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


class UserManagementViewSet(viewsets.ModelViewSet):
    """Super Admin / Admin user management. Passwords are never returned except
    the one-time generated password on create."""

    permission_classes = [IsTravelOpsAdmin]
    http_method_names = ["get", "post", "patch", "head", "options"]
    queryset = (
        User.objects.all()
        .order_by("username")
        .prefetch_related("groups", "access_scopes", "travelops_profile")
    )

    def get_serializer_class(self):
        if self.action == "create":
            return CreateUserSerializer
        if self.action == "reset_password":
            return ResetPasswordSerializer
        if self.action in ("update", "partial_update"):
            return UpdateUserSerializer
        if self.action == "retrieve":
            return ManagedUserDetailSerializer
        return ManagedUserListSerializer

    def create(self, request, *args, **kwargs):
        serializer = CreateUserSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user, generated_password = create_managed_user(
            validated_data=serializer.validated_data,
            acting_user=request.user,
            ip_address=_client_ip(request),
        )
        payload = ManagedUserDetailSerializer(user).data
        if generated_password is not None:
            # Production note: replace with an invitation/reset flow. Shown once only.
            payload["generated_password"] = generated_password
            payload["generated_password_notice"] = (
                "Copy this temporary password now. It is shown once and not stored in readable form."
            )
        return Response(payload, status=status.HTTP_201_CREATED)

    def partial_update(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = UpdateUserSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        update_managed_user(
            user=instance,
            validated_data=serializer.validated_data,
            acting_user=request.user,
            ip_address=_client_ip(request),
        )
        instance.refresh_from_db()
        return Response(ManagedUserDetailSerializer(instance).data)

    @extend_schema(request=None, responses=ManagedUserDetailSerializer)
    @action(detail=True, methods=["post"])
    def disable(self, request, pk=None):
        """Compatibility alias for deactivate."""
        user = self.get_object()
        disable_managed_user(user=user, acting_user=request.user, ip_address=_client_ip(request))
        user.refresh_from_db()
        return Response(ManagedUserDetailSerializer(user).data)

    @extend_schema(request=None, responses=ManagedUserDetailSerializer)
    @action(detail=True, methods=["post"])
    def activate(self, request, pk=None):
        user = self.get_object()
        activate_managed_user(user=user, acting_user=request.user, ip_address=_client_ip(request))
        user.refresh_from_db()
        return Response(ManagedUserDetailSerializer(user).data)

    @extend_schema(request=None, responses=ManagedUserDetailSerializer)
    @action(detail=True, methods=["post"])
    def deactivate(self, request, pk=None):
        user = self.get_object()
        disable_managed_user(user=user, acting_user=request.user, ip_address=_client_ip(request))
        user.refresh_from_db()
        return Response(ManagedUserDetailSerializer(user).data)

    @extend_schema(request=ResetPasswordSerializer, responses=ManagedUserDetailSerializer)
    @action(detail=True, methods=["post"], url_path="reset-password")
    def reset_password(self, request, pk=None):
        user = self.get_object()
        serializer = ResetPasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        reset_managed_user_password(
            user=user,
            raw_password=serializer.validated_data["password"],
            acting_user=request.user,
            ip_address=_client_ip(request),
        )
        user.refresh_from_db()
        return Response(ManagedUserDetailSerializer(user).data)

    @extend_schema(request=None, responses=ManagedUserDetailSerializer)
    @action(detail=True, methods=["post"], url_path="generate-password")
    def generate_password(self, request, pk=None):
        user = self.get_object()
        generated_password = generate_temporary_password()
        reset_managed_user_password(
            user=user,
            raw_password=generated_password,
            acting_user=request.user,
            generated=True,
            ip_address=_client_ip(request),
        )
        user.refresh_from_db()
        payload = ManagedUserDetailSerializer(user).data
        payload["generated_password"] = generated_password
        payload["generated_password_notice"] = (
            "This password is shown once. Copy it now."
        )
        return Response(payload)

    @extend_schema(request=AssignRolesSerializer, responses=ManagedUserDetailSerializer)
    @action(detail=True, methods=["post"], url_path="assign-roles")
    def assign_roles(self, request, pk=None):
        user = self.get_object()
        serializer = AssignRolesSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        assign_roles(
            user=user,
            roles=serializer.validated_data["roles"],
            acting_user=request.user,
            ip_address=_client_ip(request),
        )
        user.refresh_from_db()
        return Response(ManagedUserDetailSerializer(user).data)

    @extend_schema(responses=AccessScopeSerializer(many=True))
    @action(detail=True, methods=["get"])
    def scopes(self, request, pk=None):
        user = self.get_object()
        scopes = user.access_scopes.select_related("country", "project", "department").all()
        return Response(AccessScopeSerializer(scopes, many=True).data)


class AccessScopeViewSet(viewsets.ModelViewSet):
    """CRUD for user access scopes. Deletion deactivates instead of removing so
    the audit trail is preserved."""

    permission_classes = [IsTravelOpsAdmin]
    serializer_class = AccessScopeSerializer
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]
    queryset = UserAccessScope.objects.select_related("user", "country", "project", "department").all()
    filterset_fields = ["user", "scope_type", "is_active"]

    def perform_create(self, serializer):
        scope = serializer.save()
        log_scope_created(scope=scope, acting_user=self.request.user, ip_address=_client_ip(self.request))

    def perform_update(self, serializer):
        old_value = _scope_snapshot(serializer.instance)
        scope = serializer.save()
        log_scope_updated(
            scope=scope,
            old_value=old_value,
            acting_user=self.request.user,
            ip_address=_client_ip(self.request),
        )

    def destroy(self, request, *args, **kwargs):
        scope = self.get_object()
        deactivate_scope(scope=scope, acting_user=request.user, ip_address=_client_ip(request))
        return Response(AccessScopeSerializer(scope).data, status=status.HTTP_200_OK)


@extend_schema(responses=AvailableRoleSerializer(many=True))
@api_view(["GET"])
@permission_classes([IsTravelOpsAdmin])
def available_roles(request):
    roles = [{"name": name, "description": ROLE_DESCRIPTIONS.get(name, "")} for name in ROLE_GROUPS]
    return Response(roles)
