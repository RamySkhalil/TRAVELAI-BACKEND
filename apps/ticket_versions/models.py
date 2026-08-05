from django.conf import settings
from django.db import models

from apps.common.models import CurrencyChoices, TimeStampedModel
from apps.master_data.models import Supplier
from apps.travel_cases.models import TravelCase


class TicketAction(models.TextChoices):
    ORIGINAL = "ORIGINAL", "Original"
    REISSUE = "REISSUE", "Reissue"
    DATE_CHANGE = "DATE_CHANGE", "Date Change"
    CANCELLATION = "CANCELLATION", "Cancellation"
    REFUND = "REFUND", "Refund"
    NO_SHOW = "NO_SHOW", "No Show"
    REPLACEMENT = "REPLACEMENT", "Replacement"
    ADDITIONAL_RETURN = "ADDITIONAL_RETURN", "Additional Return"
    WRONG_UPLOAD = "WRONG_UPLOAD", "Wrong Upload"


class TicketStatus(models.TextChoices):
    DRAFT = "DRAFT", "Draft"
    ACTIVE = "ACTIVE", "Active"
    CHANGED = "CHANGED", "Changed"
    CANCELLED = "CANCELLED", "Cancelled"
    REFUNDED = "REFUNDED", "Refunded"
    NO_SHOW = "NO_SHOW", "No Show"
    LOCKED = "LOCKED", "Locked"


class TicketBillingState(models.TextChoices):
    """Where a confirmed ticket sits between booking and supplier invoicing.

    This is the payable view of a ticket and is deliberately separate from
    ``TicketStatus``, which describes the travel document itself. A ticket can
    be ``CANCELLED`` for travel purposes and still be ``AWAITING_INVOICE``
    because the supplier will bill a penalty for it.
    """

    NOT_BILLABLE = "NOT_BILLABLE", "Not Billable"
    AWAITING_INVOICE = "AWAITING_INVOICE", "Awaiting Supplier Invoice"
    INVOICED = "INVOICED", "Invoiced"


class TicketVersion(TimeStampedModel):
    travel_case = models.ForeignKey(TravelCase, on_delete=models.PROTECT, related_name="ticket_versions")
    version_number = models.CharField(max_length=10)
    ticket_action = models.CharField(max_length=30, choices=TicketAction.choices)
    passenger_name = models.CharField(max_length=180)
    ticket_number = models.CharField(max_length=80)
    pnr = models.CharField(max_length=40)
    airline = models.CharField(max_length=120, blank=True)
    route_from = models.CharField(max_length=80)
    route_to = models.CharField(max_length=80)
    departure_date = models.DateField()
    departure_time = models.TimeField(null=True, blank=True)
    arrival_date = models.DateField(null=True, blank=True)
    arrival_time = models.TimeField(null=True, blank=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=3, choices=CurrencyChoices.choices)
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name="ticket_versions")
    ticket_status = models.CharField(max_length=20, choices=TicketStatus.choices, default=TicketStatus.DRAFT)
    billing_state = models.CharField(max_length=20, choices=TicketBillingState.choices, default=TicketBillingState.NOT_BILLABLE)
    billing_state_changed_at = models.DateTimeField(null=True, blank=True)
    billing_state_note = models.TextField(blank=True)
    penalty_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    change_reason = models.TextField(blank=True)
    uploaded_ticket_file = models.FileField(upload_to="tickets/%Y/%m/", blank=True)
    extracted_data_json = models.JSONField(default=dict, blank=True)
    ai_confidence_json = models.JSONField(default=dict, blank=True)
    confirmed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="confirmed_ticket_versions")
    confirmed_at = models.DateTimeField(null=True, blank=True)
    is_locked = models.BooleanField(default=False)
    locked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["travel_case", "version_number"], name="unique_ticket_version_per_case"),
        ]
        indexes = [
            models.Index(fields=["ticket_number"]),
            models.Index(fields=["pnr"]),
            models.Index(fields=["supplier"]),
            models.Index(fields=["departure_date"]),
            models.Index(fields=["travel_case", "ticket_status"]),
            models.Index(fields=["billing_state"]),
            models.Index(fields=["billing_state", "supplier"]),
        ]
        ordering = ["travel_case", "version_number"]

    def __str__(self) -> str:
        return f"{self.travel_case.case_number} {self.version_number} - {self.ticket_number}"

# Create your models here.
