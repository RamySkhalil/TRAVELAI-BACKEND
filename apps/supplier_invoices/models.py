from django.conf import settings
from django.db import models

from apps.common.models import CurrencyChoices, TimeStampedModel, UUIDModel
from apps.master_data.models import Employee, Supplier
from apps.ticket_versions.models import TicketVersion
from apps.travel_cases.models import AccountType, TravelCase


class SupplierInvoiceStatus(models.TextChoices):
    INVOICE_RECEIVED = "INVOICE_RECEIVED", "Invoice Received"
    DATA_EXTRACTED = "DATA_EXTRACTED", "Data Extracted"
    AWAITING_MATCHING = "AWAITING_MATCHING", "Awaiting Matching"
    MATCHED = "MATCHED", "Matched"
    EXCEPTION_FOUND = "EXCEPTION_FOUND", "Exception Found"
    HR_APPROVED = "HR_APPROVED", "HR Approved"
    TBCN_GENERATED = "TBCN_GENERATED", "TBCN Generated"
    SENT_TO_FINANCE = "SENT_TO_FINANCE", "Sent to Finance"
    FINANCE_ACCEPTED = "FINANCE_ACCEPTED", "Finance Accepted"
    PAID = "PAID", "Paid"
    CLOSED = "CLOSED", "Closed"


class MatchStatus(models.TextChoices):
    UNMATCHED = "UNMATCHED", "Unmatched"
    MATCHED = "MATCHED", "Matched"
    DIFFERENCE = "DIFFERENCE", "Difference"
    EXCEPTION = "EXCEPTION", "Exception"
    APPROVED = "APPROVED", "Approved"
    REJECTED = "REJECTED", "Rejected"


class SupplierInvoice(UUIDModel, TimeStampedModel):
    invoice_record_number = models.CharField(max_length=32, unique=True)
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name="supplier_invoices")
    supplier_invoice_number = models.CharField(max_length=80)
    invoice_date = models.DateField()
    received_date = models.DateField()
    currency = models.CharField(max_length=3, choices=CurrencyChoices.choices)
    total_amount = models.DecimalField(max_digits=14, decimal_places=2)
    invoice_file = models.FileField(upload_to="supplier-invoices/%Y/%m/", blank=True)
    status = models.CharField(max_length=30, choices=SupplierInvoiceStatus.choices, default=SupplierInvoiceStatus.INVOICE_RECEIVED)
    extracted_data_json = models.JSONField(default=dict, blank=True)
    ai_confidence_json = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="created_supplier_invoices")
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="approved_supplier_invoices")
    approved_at = models.DateTimeField(null=True, blank=True)
    is_locked = models.BooleanField(default=False)
    locked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["supplier", "supplier_invoice_number"], name="unique_supplier_invoice_number_per_supplier"),
        ]
        indexes = [
            models.Index(fields=["supplier"]),
            models.Index(fields=["status"]),
            models.Index(fields=["invoice_date"]),
            models.Index(fields=["received_date"]),
            models.Index(fields=["invoice_record_number"]),
        ]
        ordering = ["-received_date", "-created_at"]

    def __str__(self) -> str:
        return f"{self.invoice_record_number} - {self.supplier_invoice_number}"


class SupplierInvoiceLine(TimeStampedModel):
    supplier_invoice = models.ForeignKey(SupplierInvoice, on_delete=models.CASCADE, related_name="lines")
    travel_case = models.ForeignKey(TravelCase, on_delete=models.PROTECT, null=True, blank=True, related_name="invoice_lines")
    ticket_version = models.ForeignKey(TicketVersion, on_delete=models.PROTECT, null=True, blank=True, related_name="invoice_lines")
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, null=True, blank=True, related_name="invoice_lines")
    ticket_number = models.CharField(max_length=80)
    route_from = models.CharField(max_length=80, blank=True)
    route_to = models.CharField(max_length=80, blank=True)
    booked_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    invoiced_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    difference_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    currency = models.CharField(max_length=3, choices=CurrencyChoices.choices)
    account_type = models.CharField(max_length=20, choices=AccountType.choices)
    match_status = models.CharField(max_length=20, choices=MatchStatus.choices, default=MatchStatus.UNMATCHED)
    exception_reason = models.TextField(blank=True)
    is_locked = models.BooleanField(default=False)

    class Meta:
        indexes = [
            models.Index(fields=["ticket_number"]),
            models.Index(fields=["match_status"]),
            models.Index(fields=["travel_case"]),
            models.Index(fields=["ticket_version"]),
            models.Index(fields=["supplier_invoice", "match_status"]),
        ]
        ordering = ["supplier_invoice", "id"]

    def __str__(self) -> str:
        return f"{self.supplier_invoice.invoice_record_number} - {self.ticket_number}"

# Create your models here.
