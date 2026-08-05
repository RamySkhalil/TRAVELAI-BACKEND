from django.conf import settings
from django.db import models

from apps.common.models import TimeStampedModel
from apps.master_data.models import Country, Department, Project


class ScopeType(models.TextChoices):
    GLOBAL = "GLOBAL", "Global"
    COUNTRY = "COUNTRY", "Country"
    PROJECT = "PROJECT", "Project"
    DEPARTMENT = "DEPARTMENT", "Department"


class TravelOpsUserProfile(TimeStampedModel):
    """Practical in-app profile for TravelOps users.

    Complements the Django user/groups model without replacing it. The
    ``is_super_admin`` flag is an explicit grant; Django superusers are always
    treated as Super Admins regardless of this flag (see access_control.permissions).
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="travelops_profile",
    )
    display_name = models.CharField(max_length=180, blank=True)
    job_title = models.CharField(max_length=120, blank=True)
    is_travelops_active = models.BooleanField(default=True)
    is_super_admin = models.BooleanField(default=False)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["user__username"]
        indexes = [
            models.Index(fields=["is_travelops_active"]),
            models.Index(fields=["is_super_admin"]),
        ]

    def __str__(self) -> str:
        return self.display_name or self.user.get_username()


class UserAccessScope(TimeStampedModel):
    """A single practical access boundary granted to a user.

    Scopes narrow the records a non Super Admin user can view inside the
    permissions their role already allows. They never widen access beyond role
    permissions and are ignored for Super Admins.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="access_scopes",
    )
    scope_type = models.CharField(max_length=20, choices=ScopeType.choices)
    country = models.ForeignKey(
        Country,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="access_scopes",
    )
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="access_scopes",
    )
    department = models.ForeignKey(
        Department,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="access_scopes",
    )
    can_view = models.BooleanField(default=True)
    can_create = models.BooleanField(default=False)
    can_approve = models.BooleanField(default=False)
    can_finance = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["user", "scope_type", "-created_at"]
        indexes = [
            models.Index(fields=["user", "is_active"]),
            models.Index(fields=["scope_type"]),
            models.Index(fields=["country"]),
            models.Index(fields=["project"]),
            models.Index(fields=["department"]),
        ]

    def __str__(self) -> str:
        target = self.country_id or self.project_id or self.department_id or "all"
        return f"{self.user_id}:{self.scope_type}:{target}"
