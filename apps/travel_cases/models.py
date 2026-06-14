from django.conf import settings
from django.db import models

from apps.common.models import TimeStampedModel, UUIDModel
from apps.master_data.models import Country, Department, Employee, Project


class TravelPurpose(models.TextChoices):
    LEAVE_ROTATION = "LEAVE_ROTATION", "Leave Rotation"
    BUSINESS_TRIP = "BUSINESS_TRIP", "Business Trip"
    NEW_HIRE = "NEW_HIRE", "New Hire"
    EMERGENCY = "EMERGENCY", "Emergency"
    PERSONAL = "PERSONAL", "Personal"
    OTHER = "OTHER", "Other"


class AccountType(models.TextChoices):
    COMPANY = "COMPANY", "Company"
    PERSONAL = "PERSONAL", "Personal"


class Priority(models.TextChoices):
    LOW = "LOW", "Low"
    NORMAL = "NORMAL", "Normal"
    HIGH = "HIGH", "High"
    URGENT = "URGENT", "Urgent"


class TravelCaseStatus(models.TextChoices):
    DRAFT = "DRAFT", "Draft"
    SUBMITTED_BY_HR = "SUBMITTED_BY_HR", "Submitted by HR"
    UNDER_BOOKING = "UNDER_BOOKING", "Under Booking"
    OPTIONS_RECEIVED = "OPTIONS_RECEIVED", "Options Received"
    APPROVED_FOR_BOOKING = "APPROVED_FOR_BOOKING", "Approved for Booking"
    TICKET_BOOKED = "TICKET_BOOKED", "Ticket Booked"
    CHANGE_REQUESTED = "CHANGE_REQUESTED", "Change Requested"
    POSTPONED = "POSTPONED", "Postponed"
    CANCELLED = "CANCELLED", "Cancelled"
    INVOICE_RECEIVED = "INVOICE_RECEIVED", "Invoice Received"
    INVOICE_MATCHED = "INVOICE_MATCHED", "Invoice Matched"
    SENT_TO_FINANCE = "SENT_TO_FINANCE", "Sent to Finance"
    PAID = "PAID", "Paid"
    CLOSED = "CLOSED", "Closed"


class TravelCase(UUIDModel, TimeStampedModel):
    case_number = models.CharField(max_length=32, unique=True)
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="travel_cases")
    badge_number = models.CharField(max_length=40)
    employee_name = models.CharField(max_length=180)
    project = models.ForeignKey(Project, on_delete=models.PROTECT, related_name="travel_cases")
    department = models.ForeignKey(Department, on_delete=models.PROTECT, related_name="travel_cases")
    country = models.ForeignKey(Country, on_delete=models.PROTECT, related_name="travel_cases")
    travel_purpose = models.CharField(max_length=30, choices=TravelPurpose.choices)
    account_type = models.CharField(max_length=20, choices=AccountType.choices)
    route_from = models.CharField(max_length=80)
    route_to = models.CharField(max_length=80)
    requested_travel_date = models.DateField()
    requested_return_date = models.DateField(null=True, blank=True)
    priority = models.CharField(max_length=20, choices=Priority.choices, default=Priority.NORMAL)
    current_status = models.CharField(max_length=30, choices=TravelCaseStatus.choices, default=TravelCaseStatus.DRAFT)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="created_travel_cases")
    assigned_to = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="assigned_travel_cases")
    approval_required = models.BooleanField(default=False)
    notes = models.TextField(blank=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["current_status"]),
            models.Index(fields=["employee"]),
            models.Index(fields=["project"]),
            models.Index(fields=["requested_travel_date"]),
            models.Index(fields=["account_type"]),
            models.Index(fields=["case_number"]),
        ]
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.case_number} - {self.employee_name}"

# Create your models here.
