from rest_framework.permissions import SAFE_METHODS, BasePermission


class IsReadOnly(BasePermission):
    def has_permission(self, request, view):
        return request.method in SAFE_METHODS


class IsGroupMember(BasePermission):
    group_name = ""

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        if user.is_superuser or user.is_staff:
            return True
        return user.groups.filter(name=self.group_name).exists()


class IsAdmin(IsGroupMember):
    group_name = "Admin"


class IsHR(IsGroupMember):
    group_name = "HR"


class IsBookingOfficer(IsGroupMember):
    group_name = "BookingOfficer"


class IsBookingManager(IsGroupMember):
    group_name = "BookingManager"


class IsFinance(IsGroupMember):
    group_name = "Finance"


class IsAuditor(IsGroupMember):
    group_name = "Auditor"


class IsAdminOrReadOnly(BasePermission):
    def has_permission(self, request, view):
        if request.method in SAFE_METHODS:
            return bool(request.user and request.user.is_authenticated)
        return IsAdmin().has_permission(request, view)


class CanViewAuditLogs(BasePermission):
    def has_permission(self, request, view):
        if request.method not in SAFE_METHODS:
            return False
        user = request.user
        if not user or not user.is_authenticated:
            return False
        if user.is_superuser or user.is_staff:
            return True
        return user.groups.filter(name__in=["Admin", "Auditor"]).exists()


class RoleBasedOperationalPermission(BasePermission):
    """Authenticated users can read; mutations require admin or configured groups."""

    write_groups: tuple[str, ...] = ()

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        if request.method in SAFE_METHODS:
            return True
        if user.is_superuser or user.is_staff:
            return True
        return user.groups.filter(name__in=self.write_groups).exists()
