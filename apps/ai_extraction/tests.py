from datetime import date

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from apps.ai_extraction.models import AiExtractionCorrection, DocumentExtractionJob, DocumentType, ExtractionStatus
from apps.ai_extraction.providers.openai_provider import OpenAIExtractionProvider
from apps.ai_extraction.services import (
    confirm_extraction_job,
    create_extraction_job,
    record_ai_correction,
    reject_extraction_job,
    run_invoice_extraction,
    run_ticket_extraction,
)
from apps.audit_logs.models import AuditLog
from apps.master_data.models import Country, Department, Employee, Project
from apps.supplier_invoices.models import SupplierInvoiceLine
from apps.ticket_versions.models import TicketVersion
from apps.travel_cases.models import AccountType, Priority, TravelCase, TravelCaseStatus, TravelPurpose


class AiExtractionProviderFoundationTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="ai-user", password="test-pass")

    def test_openai_settings_names_are_available_without_exposing_values(self):
        from django.conf import settings

        self.assertTrue(hasattr(settings, "OPENAI_API_KEY"))
        self.assertTrue(hasattr(settings, "OPENAI_MODEL"))
        self.assertTrue(hasattr(settings, "OPENAI_EMBEDDING_MODEL"))
        self.assertTrue(hasattr(settings, "OPENAI_BASE_URL"))
        self.assertTrue(hasattr(settings, "OPENAI_TIMEOUT_SECONDS"))

    @override_settings(OPENAI_API_KEY="")
    def test_missing_openai_key_produces_safe_validation_error(self):
        provider = OpenAIExtractionProvider()

        with self.assertRaises(ValidationError) as raised:
            provider.extract_ticket("ticket text")

        self.assertIn("OPENAI_API_KEY", str(raised.exception))

    def test_mock_ticket_extraction_updates_job_only(self):
        job = create_extraction_job(DocumentType.FLIGHT_TICKET, raw_text="ticket", user=self.user)

        run_ticket_extraction(job, "mock", self.user)

        job.refresh_from_db()
        self.assertEqual(job.status, ExtractionStatus.EXTRACTED)
        self.assertEqual(DocumentExtractionJob.objects.count(), 1)
        self.assertEqual(TicketVersion.objects.count(), 0)

    def test_mock_invoice_extraction_updates_job_only(self):
        job = create_extraction_job(DocumentType.SUPPLIER_INVOICE, raw_text="invoice", user=self.user)

        run_invoice_extraction(job, "mock", self.user)

        job.refresh_from_db()
        self.assertEqual(job.status, ExtractionStatus.EXTRACTED)
        self.assertEqual(DocumentExtractionJob.objects.count(), 1)
        self.assertEqual(SupplierInvoiceLine.objects.count(), 0)

    def test_ticket_extraction_stores_normalized_data(self):
        job = create_extraction_job(DocumentType.FLIGHT_TICKET, raw_text="ticket", user=self.user)

        run_ticket_extraction(job, "mock", self.user)

        job.refresh_from_db()
        self.assertEqual(job.normalized_data["document_type"], "FLIGHT_TICKET")
        self.assertEqual(job.normalized_data["ticket_number"], "1761234567890")
        self.assertIn("ticket_number", job.confidence_json)

    def test_invoice_extraction_stores_line_data_in_normalized_data(self):
        job = create_extraction_job(DocumentType.SUPPLIER_INVOICE, raw_text="invoice", user=self.user)

        run_invoice_extraction(job, "mock", self.user)

        job.refresh_from_db()
        self.assertEqual(job.normalized_data["document_type"], "SUPPLIER_INVOICE")
        self.assertEqual(job.normalized_data["lines"][0]["ticket_number"], "1761234567890")

    def test_missing_critical_ticket_fields_move_job_to_needs_review(self):
        job = create_extraction_job(DocumentType.FLIGHT_TICKET, raw_text="missing critical", user=self.user)

        run_ticket_extraction(job, "mock", self.user)

        job.refresh_from_db()
        self.assertEqual(job.status, ExtractionStatus.NEEDS_REVIEW)
        self.assertIn("ticket_number", job.missing_critical_fields)

    def test_ticket_extraction_does_not_silently_force_usd_when_currency_missing(self):
        job = create_extraction_job(DocumentType.FLIGHT_TICKET, raw_text="ticket missing currency", user=self.user)

        run_ticket_extraction(job, "mock", self.user)

        job.refresh_from_db()
        self.assertEqual(job.status, ExtractionStatus.NEEDS_REVIEW)
        self.assertEqual(job.normalized_data["currency"], "")
        self.assertIn("currency", job.missing_critical_fields)

    def test_invoice_extraction_marks_missing_line_currency_for_review(self):
        job = create_extraction_job(DocumentType.SUPPLIER_INVOICE, raw_text="invoice missing currency", user=self.user)

        run_invoice_extraction(job, "mock", self.user)

        job.refresh_from_db()
        self.assertEqual(job.status, ExtractionStatus.NEEDS_REVIEW)
        self.assertEqual(job.normalized_data["currency"], "")
        self.assertEqual(job.normalized_data["lines"][0]["currency"], "")
        self.assertIn("currency", job.missing_critical_fields)
        self.assertIn("lines.currency", job.missing_critical_fields)

    def test_confirm_extraction_job_sets_status_confirmed(self):
        job = create_extraction_job(DocumentType.FLIGHT_TICKET, raw_text="ticket", user=self.user)
        run_ticket_extraction(job, "mock", self.user)

        confirm_extraction_job(job, {"confirmed": True}, self.user)

        job.refresh_from_db()
        self.assertEqual(job.status, ExtractionStatus.CONFIRMED)
        self.assertEqual(job.normalized_data, {"confirmed": True})
        self.assertEqual(job.confirmed_by, self.user)

    def test_reject_extraction_job_sets_status_rejected(self):
        job = create_extraction_job(DocumentType.FLIGHT_TICKET, raw_text="ticket", user=self.user)
        run_ticket_extraction(job, "mock", self.user)

        reject_extraction_job(job, "Wrong document", self.user)

        job.refresh_from_db()
        self.assertEqual(job.status, ExtractionStatus.REJECTED)
        self.assertEqual(job.rejected_by, self.user)
        self.assertEqual(job.raw_extracted_data["rejection"]["reason"], "Wrong document")

    def test_ai_extraction_correction_stores_original_and_corrected_value(self):
        job = create_extraction_job(DocumentType.FLIGHT_TICKET, raw_text="ticket", user=self.user)

        correction = record_ai_correction(job, "pnr", "OLD123", "NEW123", self.user, "Manual review")

        self.assertEqual(AiExtractionCorrection.objects.count(), 1)
        self.assertEqual(correction.original_ai_value, "OLD123")
        self.assertEqual(correction.corrected_value, "NEW123")
        self.assertEqual(correction.corrected_by, self.user)

    def test_ticket_extraction_suggests_possible_travel_case_matches(self):
        self._create_matching_travel_case()
        job = create_extraction_job(DocumentType.FLIGHT_TICKET, raw_text="ticket", user=self.user)

        run_ticket_extraction(job, "mock", self.user)

        job.refresh_from_db()
        self.assertEqual(job.suggested_matches[0]["case_number"], "TRV-LY-2026-000001")
        self.assertGreater(job.suggested_matches[0]["confidence"], 0)

    def test_ai_extraction_does_not_create_ticket_version_records(self):
        job = create_extraction_job(DocumentType.FLIGHT_TICKET, raw_text="ticket", user=self.user)

        run_ticket_extraction(job, "mock", self.user)

        self.assertEqual(TicketVersion.objects.count(), 0)

    def test_ai_extraction_does_not_create_supplier_invoice_line_records(self):
        job = create_extraction_job(DocumentType.SUPPLIER_INVOICE, raw_text="invoice", user=self.user)

        run_invoice_extraction(job, "mock", self.user)

        self.assertEqual(SupplierInvoiceLine.objects.count(), 0)

    def test_audit_logs_are_created_for_extraction_lifecycle(self):
        job = create_extraction_job(DocumentType.FLIGHT_TICKET, raw_text="ticket", user=self.user)
        run_ticket_extraction(job, "mock", self.user)
        confirm_extraction_job(job, None, self.user)

        actions = set(AuditLog.objects.values_list("action", flat=True))
        self.assertIn("AI Extraction Job Created", actions)
        self.assertIn("AI Extraction Started", actions)
        self.assertIn("AI Extraction Completed", actions)
        self.assertIn("AI Extraction Confirmed", actions)

    @override_settings(OPENAI_API_KEY="")
    def test_failed_extraction_stores_safe_error_and_audit_log(self):
        job = create_extraction_job(DocumentType.FLIGHT_TICKET, raw_text="ticket", user=self.user)

        with self.assertRaises(ValidationError):
            run_ticket_extraction(job, "openai", self.user)

        job.refresh_from_db()
        self.assertEqual(job.status, ExtractionStatus.FAILED)
        self.assertIn("error", job.raw_extracted_data)
        self.assertIn("OPENAI_API_KEY", job.raw_extracted_data["error"]["message"])
        self.assertTrue(AuditLog.objects.filter(action="AI Extraction Failed").exists())

    def _create_matching_travel_case(self):
        country = Country.objects.create(code="LY", name="Libya")
        department = Department.objects.create(code="OPS", name="Operations")
        project = Project.objects.create(code="TRIP", name="Tripoli Ops", country=country)
        employee = Employee.objects.create(
            badge_number="E001",
            full_name="Aisha Mohamed",
            project=project,
            department=department,
        )
        return TravelCase.objects.create(
            case_number="TRV-LY-2026-000001",
            employee=employee,
            badge_number=employee.badge_number,
            employee_name=employee.full_name,
            project=project,
            department=department,
            country=country,
            travel_purpose=TravelPurpose.BUSINESS_TRIP,
            account_type=AccountType.COMPANY,
            route_from="CAI",
            route_to="TIP",
            requested_travel_date=date(2026, 7, 1),
            priority=Priority.NORMAL,
            current_status=TravelCaseStatus.UNDER_BOOKING,
            created_by=self.user,
        )


class AiExtractionHttpEndpointTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user_model = get_user_model()
        self.hr_user = self._create_user("hr-user", "HR")
        self.booking_user = self._create_user("booking-user", "BookingOfficer")
        self.finance_user = self._create_user("finance-user", "Finance")
        self.auditor_user = self._create_user("auditor-user", "Auditor")

    def test_unauthenticated_extract_ticket_returns_401(self):
        response = self.client.post(
            "/api/v1/ai-extraction-jobs/extract-ticket/",
            {"raw_text": "ticket", "provider": "mock"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_authenticated_hr_can_extract_ticket(self):
        self.client.force_authenticate(self.hr_user)

        response = self.client.post(
            "/api/v1/ai-extraction-jobs/extract-ticket/",
            {"raw_text": "ticket", "provider": "mock"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["document_type"], DocumentType.FLIGHT_TICKET)
        self.assertIn("id", response.data)
        self.assertEqual(DocumentExtractionJob.objects.count(), 1)

    def test_authenticated_booking_officer_can_extract_ticket(self):
        self.client.force_authenticate(self.booking_user)

        response = self.client.post(
            "/api/v1/ai-extraction-jobs/extract-ticket/",
            {"raw_text": "ticket", "provider": "mock"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], ExtractionStatus.EXTRACTED)

    def test_extract_invoice_returns_document_extraction_job(self):
        self.client.force_authenticate(self.finance_user)

        response = self.client.post(
            "/api/v1/ai-extraction-jobs/extract-invoice/",
            {"raw_text": "invoice", "provider": "mock"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["document_type"], DocumentType.SUPPLIER_INVOICE)
        self.assertEqual(response.data["status"], ExtractionStatus.EXTRACTED)
        self.assertIn("normalized_data", response.data)

    def test_confirm_endpoint_sets_status_confirmed(self):
        job = self._create_extracted_ticket_job()
        self.client.force_authenticate(self.booking_user)

        response = self.client.post(
            f"/api/v1/ai-extraction-jobs/{job.id}/confirm/",
            {"corrected_data": {"confirmed": True}},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], ExtractionStatus.CONFIRMED)
        job.refresh_from_db()
        self.assertEqual(job.status, ExtractionStatus.CONFIRMED)
        self.assertEqual(job.confirmed_by, self.booking_user)

    def test_reject_endpoint_sets_status_rejected(self):
        job = self._create_extracted_ticket_job()
        self.client.force_authenticate(self.booking_user)

        response = self.client.post(
            f"/api/v1/ai-extraction-jobs/{job.id}/reject/",
            {"reason": "Wrong document"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], ExtractionStatus.REJECTED)
        job.refresh_from_db()
        self.assertEqual(job.status, ExtractionStatus.REJECTED)
        self.assertEqual(job.rejected_by, self.booking_user)

    def test_extract_ticket_endpoint_does_not_create_ticket_version(self):
        self.client.force_authenticate(self.booking_user)

        response = self.client.post(
            "/api/v1/ai-extraction-jobs/extract-ticket/",
            {"raw_text": "ticket", "provider": "mock"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(TicketVersion.objects.count(), 0)

    def test_extract_invoice_endpoint_does_not_create_supplier_invoice_line(self):
        self.client.force_authenticate(self.finance_user)

        response = self.client.post(
            "/api/v1/ai-extraction-jobs/extract-invoice/",
            {"raw_text": "invoice", "provider": "mock"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(SupplierInvoiceLine.objects.count(), 0)

    def test_auth_me_returns_current_user_details(self):
        self.client.force_authenticate(self.hr_user)

        response = self.client.get("/api/v1/auth/me/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["username"], "hr-user")
        self.assertIn("HR", response.data["groups"])
        self.assertIn("permissions", response.data)
        self.assertFalse(response.data["is_superuser"])

    def test_auditor_cannot_write_ai_extraction_jobs(self):
        self.client.force_authenticate(self.auditor_user)

        response = self.client.post(
            "/api/v1/ai-extraction-jobs/extract-ticket/",
            {"raw_text": "ticket", "provider": "mock"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def _create_user(self, username, group_name):
        user = self.user_model.objects.create_user(username=username, password="test-pass")
        group, _ = Group.objects.get_or_create(name=group_name)
        user.groups.add(group)
        return user

    def _create_extracted_ticket_job(self):
        job = create_extraction_job(DocumentType.FLIGHT_TICKET, raw_text="ticket", user=self.booking_user)
        run_ticket_extraction(job, "mock", self.booking_user)
        job.refresh_from_db()
        return job
