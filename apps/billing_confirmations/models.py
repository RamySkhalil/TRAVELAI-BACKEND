from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone

from apps.common.models import TimeStampedModel, UUIDModel
from apps.master_data.models import Supplier
from apps.supplier_invoices.models import SupplierInvoice


class BillingConfirmationStatus(models.TextChoices):
    DRAFT = "DRAFT", "Draft"
    GENERATED = "GENERATED", "Generated"
    REVIEWED = "REVIEWED", "Reviewed"
    SENT_TO_FINANCE = "SENT_TO_FINANCE", "Sent to Finance"
    FINANCE_ACCEPTED = "FINANCE_ACCEPTED", "Finance Accepted"
    PAID = "PAID", "Paid"
    CANCELLED = "CANCELLED", "Cancelled"
    REVISED = "REVISED", "Revised"


class FinanceStatus(models.TextChoices):
    NOT_SENT = "NOT_SENT", "Not Sent"
    SENT = "SENT", "Sent"
    ACCEPTED = "ACCEPTED", "Accepted"
    REJECTED = "REJECTED", "Rejected"
    PAID = "PAID", "Paid"


class TravelBillingConfirmationNote(UUIDModel, TimeStampedModel):
    confirmation_no = models.CharField(max_length=36, unique=True)
    supplier_invoice = models.ForeignKey(SupplierInvoice, on_delete=models.PROTECT, related_name="billing_confirmations")
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name="billing_confirmations")
    supplier_invoice_number = models.CharField(max_length=80)
    total_amount = models.DecimalField(max_digits=14, decimal_places=2)
    matched_amount = models.DecimalField(max_digits=14, decimal_places=2)
    difference_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    currency = models.CharField(max_length=3, default="USD")
    status = models.CharField(max_length=30, choices=BillingConfirmationStatus.choices, default=BillingConfirmationStatus.DRAFT)
    generated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="generated_tbcn")
    generated_at = models.DateTimeField(default=timezone.now)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="reviewed_tbcn")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    sent_to_finance_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="sent_tbcn")
    sent_to_finance_at = models.DateTimeField(null=True, blank=True)
    finance_status = models.CharField(max_length=20, choices=FinanceStatus.choices, default=FinanceStatus.NOT_SENT)
    pdf_file = models.FileField(upload_to="tbcn/%Y/%m/", blank=True)
    is_locked = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["supplier_invoice"],
                condition=~Q(status__in=[BillingConfirmationStatus.CANCELLED, BillingConfirmationStatus.REVISED]),
                name="unique_active_tbcn_per_supplier_invoice",
            ),
        ]
        indexes = [
            models.Index(fields=["confirmation_no"]),
            models.Index(fields=["supplier_invoice"]),
            models.Index(fields=["finance_status"]),
            models.Index(fields=["status"]),
        ]
        ordering = ["-generated_at"]

    def __str__(self) -> str:
        return self.confirmation_no


class TravelBillingConfirmationRevision(TimeStampedModel):
    original_tbcn = models.ForeignKey(TravelBillingConfirmationNote, on_delete=models.PROTECT, related_name="revisions")
    revision_number = models.PositiveIntegerField()
    reason = models.TextField()
    revised_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="tbcn_revisions")
    revised_at = models.DateTimeField(default=timezone.now)
    snapshot_json = models.JSONField(default=dict, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["original_tbcn", "revision_number"], name="unique_tbcn_revision_number"),
        ]
        ordering = ["original_tbcn", "revision_number"]

    def __str__(self) -> str:
        return f"{self.original_tbcn.confirmation_no} R{self.revision_number}"

# Create your models here.
