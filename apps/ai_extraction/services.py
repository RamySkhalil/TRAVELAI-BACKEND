from __future__ import annotations

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.audit_logs.services import create_audit_log
from apps.travel_cases.models import TravelCase, TravelCaseStatus

from .models import AiExtractionCorrection, DocumentExtractionJob, DocumentType, ExtractionStatus
from .providers import MockExtractionProvider, OpenAIExtractionProvider


SOURCE_TEXT_KEY = "_source_text"
PROVIDERS = {
    "mock": MockExtractionProvider,
    "openai": OpenAIExtractionProvider,
}


def create_extraction_job(document_type, source_file=None, raw_text: str = "", user=None) -> DocumentExtractionJob:
    if not source_file and not raw_text:
        raise ValidationError("Either source_file or raw_text is required.")

    raw_data = {SOURCE_TEXT_KEY: raw_text} if raw_text else {}
    create_kwargs = {
        "document_type": document_type or DocumentType.UNKNOWN,
        "status": ExtractionStatus.UPLOADED,
        "raw_extracted_data": raw_data,
        "created_by": user,
    }
    if source_file:
        create_kwargs["source_file"] = source_file
    job = DocumentExtractionJob.objects.create(**create_kwargs)
    create_audit_log(
        user=user,
        action="AI Extraction Job Created",
        entity_type="DocumentExtractionJob",
        entity_id=job.id,
        new_value={"document_type": job.document_type, "status": job.status},
    )
    return job


def run_ticket_extraction(job: DocumentExtractionJob, provider_name: str = "mock", user=None) -> DocumentExtractionJob:
    return _run_extraction(job, provider_name, user, document_type=DocumentType.FLIGHT_TICKET)


def run_invoice_extraction(job: DocumentExtractionJob, provider_name: str = "mock", user=None) -> DocumentExtractionJob:
    return _run_extraction(job, provider_name, user, document_type=DocumentType.SUPPLIER_INVOICE)


def confirm_extraction_job(job: DocumentExtractionJob, corrected_data=None, user=None) -> DocumentExtractionJob:
    if job.status not in {ExtractionStatus.EXTRACTED, ExtractionStatus.NEEDS_REVIEW}:
        raise ValidationError("Only extracted jobs can be confirmed.")

    old_value = {"status": job.status, "normalized_data": job.normalized_data}
    if corrected_data is not None:
        job.normalized_data = corrected_data
    job.status = ExtractionStatus.CONFIRMED
    job.confirmed_by = user
    job.confirmed_at = timezone.now()
    job.save(update_fields=["normalized_data", "status", "confirmed_by", "confirmed_at", "updated_at"])
    create_audit_log(
        user=user,
        action="AI Extraction Confirmed",
        entity_type="DocumentExtractionJob",
        entity_id=job.id,
        old_value=old_value,
        new_value={"status": job.status, "confirmed_by": user.id if user else None},
    )
    return job


def reject_extraction_job(job: DocumentExtractionJob, reason: str, user=None) -> DocumentExtractionJob:
    if job.status not in {ExtractionStatus.UPLOADED, ExtractionStatus.EXTRACTED, ExtractionStatus.NEEDS_REVIEW, ExtractionStatus.FAILED}:
        raise ValidationError("This extraction job cannot be rejected.")
    if not reason:
        raise ValidationError("Rejection reason is required.")

    old_status = job.status
    job.status = ExtractionStatus.REJECTED
    job.rejected_by = user
    job.rejected_at = timezone.now()
    raw_data = dict(job.raw_extracted_data or {})
    raw_data["rejection"] = {"reason": reason}
    job.raw_extracted_data = raw_data
    job.save(update_fields=["status", "rejected_by", "rejected_at", "raw_extracted_data", "updated_at"])
    create_audit_log(
        user=user,
        action="AI Extraction Rejected",
        entity_type="DocumentExtractionJob",
        entity_id=job.id,
        old_value={"status": old_status},
        new_value={"status": job.status, "reason": reason},
    )
    return job


def record_ai_correction(
    job: DocumentExtractionJob,
    field_name: str,
    original_ai_value,
    corrected_value,
    user=None,
    correction_reason: str | None = None,
) -> AiExtractionCorrection:
    correction = AiExtractionCorrection.objects.create(
        extraction_job=job,
        field_name=field_name,
        original_ai_value=original_ai_value,
        corrected_value=corrected_value,
        corrected_by=user,
        correction_reason=correction_reason or "",
    )
    create_audit_log(
        user=user,
        action="AI Extraction Correction Recorded",
        entity_type="AiExtractionCorrection",
        entity_id=correction.id,
        old_value={field_name: original_ai_value},
        new_value={field_name: corrected_value},
        metadata={"extraction_job": job.id, "correction_reason": correction.correction_reason},
    )
    return correction


def record_correction(
    *,
    extraction_job: DocumentExtractionJob,
    field_name: str,
    original_ai_value,
    corrected_value,
    corrected_by=None,
    correction_reason: str = "",
) -> AiExtractionCorrection:
    return record_ai_correction(
        extraction_job,
        field_name,
        original_ai_value,
        corrected_value,
        corrected_by,
        correction_reason,
    )


def suggest_travel_case_matches(normalized_ticket_data: dict) -> list[dict]:
    passenger_name = _normalize(normalized_ticket_data.get("passenger_name"))
    route_from = _normalize(normalized_ticket_data.get("route_from"))
    route_to = _normalize(normalized_ticket_data.get("route_to"))
    departure_date = normalized_ticket_data.get("departure_date")
    open_statuses = [
        status
        for status in TravelCaseStatus.values
        if status not in {TravelCaseStatus.CANCELLED, TravelCaseStatus.PAID, TravelCaseStatus.CLOSED}
    ]

    suggestions = []
    travel_cases = TravelCase.objects.filter(current_status__in=open_statuses).select_related("employee").order_by("-created_at")[:100]
    for travel_case in travel_cases:
        score = 0.0
        reasons = []
        if passenger_name and passenger_name == _normalize(travel_case.employee_name):
            score += 0.4
            reasons.append("passenger_name")
        if route_from and route_to and route_from == _normalize(travel_case.route_from) and route_to == _normalize(travel_case.route_to):
            score += 0.4
            reasons.append("route")
        if departure_date and str(travel_case.requested_travel_date) == str(departure_date):
            score += 0.2
            reasons.append("requested_travel_date")
        if score > 0:
            suggestions.append(
                {
                    "travel_case_id": travel_case.id,
                    "travel_case_uid": str(travel_case.uid),
                    "case_number": travel_case.case_number,
                    "employee_name": travel_case.employee_name,
                    "route_from": travel_case.route_from,
                    "route_to": travel_case.route_to,
                    "requested_travel_date": str(travel_case.requested_travel_date),
                    "current_status": travel_case.current_status,
                    "confidence": round(score, 2),
                    "match_reasons": reasons,
                }
            )
    return sorted(suggestions, key=lambda item: item["confidence"], reverse=True)[:5]


def get_provider(provider_name: str):
    provider_class = PROVIDERS.get(provider_name or "mock")
    if not provider_class:
        raise ValidationError("Unsupported extraction provider.")
    return provider_class()


def _run_extraction(job: DocumentExtractionJob, provider_name: str, user, document_type: str) -> DocumentExtractionJob:
    if job.status not in {ExtractionStatus.UPLOADED, ExtractionStatus.FAILED}:
        raise ValidationError("Only uploaded or failed extraction jobs can be processed.")

    provider = get_provider(provider_name)
    old_status = job.status
    with transaction.atomic():
        job.status = ExtractionStatus.PROCESSING
        job.document_type = document_type
        job.save(update_fields=["status", "document_type", "updated_at"])
        create_audit_log(
            user=user,
            action="AI Extraction Started",
            entity_type="DocumentExtractionJob",
            entity_id=job.id,
            old_value={"status": old_status},
            new_value={"status": job.status, "provider": provider.name},
        )

    try:
        document_text = _get_job_text(job)
        normalized_data = provider.extract_ticket(document_text) if document_type == DocumentType.FLIGHT_TICKET else provider.extract_invoice(document_text)
        missing_fields = normalized_data.get("missing_critical_fields", [])
        suggested_matches = suggest_travel_case_matches(normalized_data) if document_type == DocumentType.FLIGHT_TICKET else []
        job.raw_extracted_data = {"provider": provider.name, "result": normalized_data}
        job.normalized_data = normalized_data
        job.confidence_json = normalized_data.get("confidence", {})
        job.missing_critical_fields = missing_fields
        job.suggested_matches = suggested_matches
        job.status = ExtractionStatus.NEEDS_REVIEW if missing_fields else ExtractionStatus.EXTRACTED
        job.save(
            update_fields=[
                "raw_extracted_data",
                "normalized_data",
                "confidence_json",
                "missing_critical_fields",
                "suggested_matches",
                "status",
                "updated_at",
            ]
        )
        create_audit_log(
            user=user,
            action="AI Extraction Completed",
            entity_type="DocumentExtractionJob",
            entity_id=job.id,
            new_value={"status": job.status, "missing_critical_fields": missing_fields, "provider": provider.name},
        )
    except Exception as exc:
        safe_error = _safe_error(exc)
        job.status = ExtractionStatus.FAILED
        job.raw_extracted_data = {"provider": provider.name, "error": safe_error}
        job.save(update_fields=["status", "raw_extracted_data", "updated_at"])
        create_audit_log(
            user=user,
            action="AI Extraction Failed",
            entity_type="DocumentExtractionJob",
            entity_id=job.id,
            new_value={"status": job.status, "error": safe_error, "provider": provider.name},
        )
        if isinstance(exc, ValidationError):
            raise
    return job


def _get_job_text(job: DocumentExtractionJob) -> str:
    raw_data = job.raw_extracted_data or {}
    if raw_data.get(SOURCE_TEXT_KEY):
        return raw_data[SOURCE_TEXT_KEY]
    if not job.source_file:
        raise ValidationError("Document text is required when no source file is available.")
    try:
        job.source_file.open("rb")
        content = job.source_file.read()
    finally:
        job.source_file.close()
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValidationError("Only plain text source files are supported before OCR is implemented.") from exc


def _safe_error(exc: Exception) -> dict:
    detail = getattr(exc, "detail", None)
    if detail:
        message = str(detail)
    else:
        message = str(exc) or exc.__class__.__name__
    return {"message": message[:500], "type": exc.__class__.__name__}


def _normalize(value) -> str:
    return " ".join(str(value or "").strip().lower().split())
