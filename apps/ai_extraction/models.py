from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.common.models import TimeStampedModel, UUIDModel


class DocumentType(models.TextChoices):
    FLIGHT_TICKET = "FLIGHT_TICKET", "Flight Ticket"
    CHANGED_TICKET = "CHANGED_TICKET", "Changed Ticket"
    REISSUED_TICKET = "REISSUED_TICKET", "Reissued Ticket"
    CANCELLATION = "CANCELLATION", "Cancellation"
    REFUND_NOTE = "REFUND_NOTE", "Refund Note"
    SUPPLIER_INVOICE = "SUPPLIER_INVOICE", "Supplier Invoice"
    PERMIT_DOCUMENT = "PERMIT_DOCUMENT", "Permit Document"
    UNKNOWN = "UNKNOWN", "Unknown"


class ExtractionStatus(models.TextChoices):
    UPLOADED = "UPLOADED", "Uploaded"
    PROCESSING = "PROCESSING", "Processing"
    EXTRACTED = "EXTRACTED", "Extracted"
    NEEDS_REVIEW = "NEEDS_REVIEW", "Needs Review"
    CONFIRMED = "CONFIRMED", "Confirmed"
    REJECTED = "REJECTED", "Rejected"
    FAILED = "FAILED", "Failed"


class DocumentExtractionJob(UUIDModel, TimeStampedModel):
    document_type = models.CharField(max_length=30, choices=DocumentType.choices, default=DocumentType.UNKNOWN)
    source_file = models.FileField(upload_to="ai-extraction/%Y/%m/")
    status = models.CharField(max_length=20, choices=ExtractionStatus.choices, default=ExtractionStatus.UPLOADED)
    raw_extracted_data = models.JSONField(default=dict, blank=True)
    normalized_data = models.JSONField(default=dict, blank=True)
    confidence_json = models.JSONField(default=dict, blank=True)
    missing_critical_fields = models.JSONField(default=list, blank=True)
    suggested_matches = models.JSONField(default=list, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="created_extraction_jobs")
    confirmed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="confirmed_extraction_jobs")
    confirmed_at = models.DateTimeField(null=True, blank=True)
    rejected_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="rejected_extraction_jobs")
    rejected_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["document_type"]),
            models.Index(fields=["status"]),
            models.Index(fields=["created_at"]),
        ]
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.get_document_type_display()} - {self.get_status_display()}"


class AiExtractionCorrection(models.Model):
    extraction_job = models.ForeignKey(DocumentExtractionJob, on_delete=models.CASCADE, related_name="corrections")
    field_name = models.CharField(max_length=120)
    original_ai_value = models.JSONField(null=True, blank=True)
    corrected_value = models.JSONField(null=True, blank=True)
    corrected_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="ai_extraction_corrections")
    corrected_at = models.DateTimeField(default=timezone.now)
    correction_reason = models.TextField(blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["extraction_job", "field_name"]),
            models.Index(fields=["corrected_by"]),
            models.Index(fields=["corrected_at"]),
        ]
        ordering = ["-corrected_at"]

    def __str__(self) -> str:
        return f"{self.extraction_job_id} - {self.field_name}"

# Create your models here.
