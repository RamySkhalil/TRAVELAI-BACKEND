from django.conf import settings
from django.db import models

from apps.common.models import TimeStampedModel
from apps.travel_cases.models import TravelCase


class PermitType(models.TextChoices):
    EGYPT_PERMIT = "EGYPT_PERMIT", "Egypt Permit"
    LIBYA_PERMIT = "LIBYA_PERMIT", "Libya Permit"


class PermitStatus(models.TextChoices):
    PENDING = "PENDING", "Pending"
    APPROVED = "APPROVED", "Approved"
    REJECTED = "REJECTED", "Rejected"
    EXPIRED = "EXPIRED", "Expired"
    NOT_REQUIRED = "NOT_REQUIRED", "Not Required"


class Permit(TimeStampedModel):
    travel_case = models.ForeignKey(TravelCase, on_delete=models.PROTECT, related_name="permits")
    permit_type = models.CharField(max_length=30, choices=PermitType.choices)
    status = models.CharField(max_length=20, choices=PermitStatus.choices, default=PermitStatus.PENDING)
    issue_date = models.DateField(null=True, blank=True)
    expiry_date = models.DateField(null=True, blank=True)
    attachment = models.FileField(upload_to="permits/%Y/%m/", blank=True)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="created_permits")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["travel_case", "permit_type"], name="unique_permit_type_per_case"),
        ]
        indexes = [
            models.Index(fields=["permit_type"]),
            models.Index(fields=["status"]),
            models.Index(fields=["expiry_date"]),
        ]
        ordering = ["travel_case", "permit_type"]

    def __str__(self) -> str:
        return f"{self.travel_case.case_number} - {self.get_permit_type_display()}"

# Create your models here.
