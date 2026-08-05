from datetime import date

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from apps.ai_extraction.models import DocumentExtractionJob, DocumentType, ExtractionStatus
from apps.audit_logs.models import AuditLog
from apps.master_data.models import Country, Department, Employee, Project, Supplier
from apps.ticket_versions.models import TicketAction, TicketBillingState, TicketStatus, TicketVersion
from apps.travel_cases.models import AccountType, Priority, TravelCase, TravelCaseStatus, TravelPurpose


class TicketVersionExtractionWorkflowTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user_model = get_user_model()
        self.booking_user = self._create_user("booking-user", "BookingOfficer")
        self.client.force_authenticate(self.booking_user)
        self.supplier = Supplier.objects.create(code="AFR", name="Afriqiyah Airways")
        self.travel_case = self._create_travel_case()

    def test_cannot_create_ticket_version_from_unconfirmed_extraction_job(self):
        job = self._create_extraction_job(status=ExtractionStatus.EXTRACTED)

        response = self.client.post(
            "/api/v1/ticket-versions/create-from-extraction/",
            self._create_payload(job, self.travel_case),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(TicketVersion.objects.count(), 0)

    def test_confirmed_extraction_creates_v1_for_first_ticket(self):
        job = self._create_extraction_job()

        response = self.client.post(
            "/api/v1/ticket-versions/create-from-extraction/",
            self._create_payload(job, self.travel_case),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ticket = TicketVersion.objects.get()
        self.assertEqual(ticket.version_number, "V1")
        self.assertEqual(ticket.travel_case, self.travel_case)
        self.assertEqual(ticket.ticket_number, "1761234567890")
        self.assertEqual(ticket.currency, "USD")

    def test_ticket_version_supports_usd(self):
        job = self._create_extraction_job(currency="USD")

        response = self.client.post(
            "/api/v1/ticket-versions/create-from-extraction/",
            self._create_payload(job, self.travel_case),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["currency"], "USD")

    def test_ticket_version_supports_egp(self):
        job = self._create_extraction_job(currency="EGP")

        response = self.client.post(
            "/api/v1/ticket-versions/create-from-extraction/",
            self._create_payload(job, self.travel_case),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["currency"], "EGP")

    def test_missing_currency_blocks_ticket_version_creation(self):
        job = self._create_extraction_job(currency="")

        response = self.client.post(
            "/api/v1/ticket-versions/create-from-extraction/",
            self._create_payload(job, self.travel_case),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(TicketVersion.objects.count(), 0)

    def test_second_confirmed_extraction_creates_v2(self):
        first_job = self._create_extraction_job(ticket_number="1761234567890")
        second_job = self._create_extraction_job(ticket_number="1761234567891")

        self.client.post(
            "/api/v1/ticket-versions/create-from-extraction/",
            self._create_payload(first_job, self.travel_case),
            format="json",
        )
        response = self.client.post(
            "/api/v1/ticket-versions/create-from-extraction/",
            self._create_payload(second_job, self.travel_case, TicketAction.REISSUE),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["version_number"], "V2")

    def test_ticket_version_creation_does_not_overwrite_v1(self):
        first_job = self._create_extraction_job(ticket_number="1761234567890")
        second_job = self._create_extraction_job(ticket_number="1761234567891")

        self.client.post(
            "/api/v1/ticket-versions/create-from-extraction/",
            self._create_payload(first_job, self.travel_case),
            format="json",
        )
        v1_id = TicketVersion.objects.get(version_number="V1").id
        self.client.post(
            "/api/v1/ticket-versions/create-from-extraction/",
            self._create_payload(second_job, self.travel_case, TicketAction.DATE_CHANGE),
            format="json",
        )

        v1 = TicketVersion.objects.get(id=v1_id)
        self.assertEqual(v1.version_number, "V1")
        self.assertEqual(v1.ticket_number, "1761234567890")
        self.assertEqual(TicketVersion.objects.count(), 2)

    def test_missing_travel_case_blocks_creation(self):
        job = self._create_extraction_job()

        response = self.client.post(
            "/api/v1/ticket-versions/create-from-extraction/",
            self._create_payload(job, travel_case_id=999999),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(TicketVersion.objects.count(), 0)

    def test_cancel_ticket_requires_reason(self):
        ticket = self._create_ticket_version()

        response = self.client.post(
            f"/api/v1/ticket-versions/{ticket.id}/mark-cancelled/",
            {"reason": ""},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        ticket.refresh_from_db()
        self.assertEqual(ticket.ticket_status, TicketStatus.DRAFT)

    def test_confirmed_extraction_attaches_source_document_to_ticket(self):
        job = self._create_extraction_job()
        job.source_file.save("eticket.pdf", SimpleUploadedFile("eticket.pdf", b"%PDF-1.4 ticket bytes"), save=True)

        response = self.client.post(
            "/api/v1/ticket-versions/create-from-extraction/",
            self._create_payload(job, self.travel_case),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ticket = TicketVersion.objects.get()
        self.assertTrue(ticket.uploaded_ticket_file.name)
        self.assertTrue(ticket.uploaded_ticket_file.name.endswith(".pdf"))
        self.assertIsNotNone(response.data["uploaded_ticket_file_url"])
        self.assertTrue(
            AuditLog.objects.filter(action="Ticket Document Attached", entity_id=ticket.id).exists()
        )
        ticket.uploaded_ticket_file.delete(save=False)
        job.source_file.delete(save=False)

    def test_ticket_version_exposes_supplier_and_confirmed_by_names(self):
        ticket = self._create_ticket_version()

        response = self.client.post(f"/api/v1/ticket-versions/{ticket.id}/confirm/", {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["supplier_name"], self.supplier.name)
        self.assertEqual(response.data["confirmed_by_username"], self.booking_user.username)

    def test_create_from_extraction_writes_audit_log(self):
        job = self._create_extraction_job()

        response = self.client.post(
            "/api/v1/ticket-versions/create-from-extraction/",
            self._create_payload(job, self.travel_case),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(
            AuditLog.objects.filter(
                action="Ticket Version Created From Extraction",
                entity_type="TicketVersion",
                entity_id=response.data["id"],
            ).exists()
        )

    def test_create_from_extraction_advances_case_to_ticket_booked(self):
        self.assertEqual(self.travel_case.current_status, TravelCaseStatus.UNDER_BOOKING)
        job = self._create_extraction_job()

        response = self.client.post(
            "/api/v1/ticket-versions/create-from-extraction/",
            self._create_payload(job, self.travel_case),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.travel_case.refresh_from_db()
        self.assertEqual(self.travel_case.current_status, TravelCaseStatus.TICKET_BOOKED)
        self.assertTrue(
            AuditLog.objects.filter(
                action="Travel Case Ticket Booked",
                entity_type="TravelCase",
                entity_id=self.travel_case.id,
            ).exists()
        )

    def test_ticket_upload_does_not_regress_finalized_case(self):
        self.travel_case.current_status = TravelCaseStatus.PAID
        self.travel_case.save(update_fields=["current_status"])
        job = self._create_extraction_job()

        response = self.client.post(
            "/api/v1/ticket-versions/create-from-extraction/",
            self._create_payload(job, self.travel_case),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.travel_case.refresh_from_db()
        self.assertEqual(self.travel_case.current_status, TravelCaseStatus.PAID)
        self.assertFalse(
            AuditLog.objects.filter(
                action="Travel Case Ticket Booked",
                entity_id=self.travel_case.id,
            ).exists()
        )

    def test_second_ticket_version_does_not_duplicate_ticket_booked_transition(self):
        first_job = self._create_extraction_job(ticket_number="1761234567890")
        second_job = self._create_extraction_job(ticket_number="1761234567891")

        self.client.post(
            "/api/v1/ticket-versions/create-from-extraction/",
            self._create_payload(first_job, self.travel_case),
            format="json",
        )
        self.client.post(
            "/api/v1/ticket-versions/create-from-extraction/",
            self._create_payload(second_job, self.travel_case, TicketAction.REISSUE),
            format="json",
        )

        self.travel_case.refresh_from_db()
        self.assertEqual(self.travel_case.current_status, TravelCaseStatus.TICKET_BOOKED)
        self.assertEqual(
            AuditLog.objects.filter(
                action="Travel Case Ticket Booked",
                entity_id=self.travel_case.id,
            ).count(),
            1,
        )

    def test_draft_ticket_from_extraction_is_not_yet_a_payable(self):
        job = self._create_extraction_job()

        response = self.client.post(
            "/api/v1/ticket-versions/create-from-extraction/",
            self._create_payload(job, self.travel_case),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ticket = TicketVersion.objects.get()
        self.assertEqual(ticket.ticket_status, TicketStatus.DRAFT)
        self.assertEqual(ticket.billing_state, TicketBillingState.NOT_BILLABLE)
        self.assertIsNone(ticket.billing_state_changed_at)

    def test_confirming_a_ticket_opens_the_payable(self):
        ticket = self._create_ticket_version()

        response = self.client.post(f"/api/v1/ticket-versions/{ticket.id}/confirm/", {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ticket.refresh_from_db()
        self.assertEqual(ticket.billing_state, TicketBillingState.AWAITING_INVOICE)
        self.assertIsNotNone(ticket.billing_state_changed_at)
        self.assertTrue(
            AuditLog.objects.filter(
                action="Ticket Awaiting Supplier Invoice",
                entity_type="TicketVersion",
                entity_id=ticket.id,
            ).exists()
        )

    def test_wrongly_uploaded_ticket_never_becomes_a_payable(self):
        ticket = self._create_ticket_version()
        ticket.ticket_action = TicketAction.WRONG_UPLOAD
        ticket.save(update_fields=["ticket_action"])

        self.client.post(f"/api/v1/ticket-versions/{ticket.id}/confirm/", {}, format="json")

        ticket.refresh_from_db()
        self.assertEqual(ticket.billing_state, TicketBillingState.NOT_BILLABLE)

    def test_billing_state_cannot_be_written_directly(self):
        ticket = self._create_ticket_version()

        response = self.client.patch(
            f"/api/v1/ticket-versions/{ticket.id}/",
            {"billing_state": TicketBillingState.INVOICED},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ticket.refresh_from_db()
        self.assertEqual(ticket.billing_state, TicketBillingState.NOT_BILLABLE)

    def test_mark_not_billable_requires_a_reason(self):
        ticket = self._create_ticket_version()
        self.client.post(f"/api/v1/ticket-versions/{ticket.id}/confirm/", {}, format="json")

        response = self.client.post(
            f"/api/v1/ticket-versions/{ticket.id}/mark-not-billable/",
            {"reason": "  "},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        ticket.refresh_from_db()
        self.assertEqual(ticket.billing_state, TicketBillingState.AWAITING_INVOICE)

    def test_mark_not_billable_closes_the_payable_with_an_audited_reason(self):
        ticket = self._create_ticket_version()
        self.client.post(f"/api/v1/ticket-versions/{ticket.id}/confirm/", {}, format="json")

        response = self.client.post(
            f"/api/v1/ticket-versions/{ticket.id}/mark-not-billable/",
            {"reason": "Cancelled with no penalty"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ticket.refresh_from_db()
        self.assertEqual(ticket.billing_state, TicketBillingState.NOT_BILLABLE)
        self.assertEqual(ticket.billing_state_note, "Cancelled with no penalty")
        self.assertTrue(
            AuditLog.objects.filter(
                action="Ticket Marked Not Billable",
                entity_type="TicketVersion",
                entity_id=ticket.id,
            ).exists()
        )

    def test_ticket_marked_not_billable_is_not_reopened_by_a_later_confirm(self):
        ticket = self._create_ticket_version()
        self.client.post(f"/api/v1/ticket-versions/{ticket.id}/confirm/", {}, format="json")
        self.client.post(
            f"/api/v1/ticket-versions/{ticket.id}/mark-not-billable/",
            {"reason": "Duplicate booking"},
            format="json",
        )

        self.client.post(f"/api/v1/ticket-versions/{ticket.id}/confirm/", {}, format="json")

        ticket.refresh_from_db()
        self.assertEqual(ticket.billing_state, TicketBillingState.NOT_BILLABLE)

    def test_invoiced_ticket_cannot_be_marked_not_billable(self):
        ticket = self._create_ticket_version()
        ticket.billing_state = TicketBillingState.INVOICED
        ticket.save(update_fields=["billing_state"])

        response = self.client.post(
            f"/api/v1/ticket-versions/{ticket.id}/mark-not-billable/",
            {"reason": "Too late"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        ticket.refresh_from_db()
        self.assertEqual(ticket.billing_state, TicketBillingState.INVOICED)

    def test_tickets_can_be_filtered_by_billing_state(self):
        awaiting = self._create_ticket_version()
        self.client.post(f"/api/v1/ticket-versions/{awaiting.id}/confirm/", {}, format="json")

        response = self.client.get("/api/v1/ticket-versions/?billing_state=AWAITING_INVOICE")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([row["id"] for row in response.data], [awaiting.id])

    def _create_user(self, username, group_name):
        user = self.user_model.objects.create_user(username=username, password="test-pass")
        group, _ = Group.objects.get_or_create(name=group_name)
        user.groups.add(group)
        return user

    def _create_travel_case(self):
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
            created_by=self.booking_user,
        )

    def _create_extraction_job(self, status=ExtractionStatus.CONFIRMED, ticket_number="1761234567890", currency="USD"):
        return DocumentExtractionJob.objects.create(
            document_type=DocumentType.FLIGHT_TICKET,
            status=status,
            normalized_data={
                "document_type": "FLIGHT_TICKET",
                "passenger_name": "Aisha Mohamed",
                "ticket_number": ticket_number,
                "pnr": "ABC123",
                "airline": "Afriqiyah Airways",
                "route_from": "CAI",
                "route_to": "TIP",
                "departure_date": "2026-07-01",
                "departure_time": "10:00",
                "arrival_date": "2026-07-01",
                "arrival_time": "12:00",
                "amount": "450.00",
                "currency": currency,
                "supplier": "Afriqiyah Airways",
                "confidence": {"ticket_number": 0.98},
                "missing_critical_fields": [],
            },
            confidence_json={"ticket_number": 0.98},
            created_by=self.booking_user,
            confirmed_by=self.booking_user if status == ExtractionStatus.CONFIRMED else None,
        )

    def _create_payload(self, job, travel_case=None, ticket_action=TicketAction.ORIGINAL, travel_case_id=None):
        return {
            "extraction_job": job.id,
            "travel_case": travel_case_id or travel_case.id,
            "ticket_action": ticket_action,
            "overrides": {},
        }

    def _create_ticket_version(self):
        return TicketVersion.objects.create(
            travel_case=self.travel_case,
            version_number="V1",
            ticket_action=TicketAction.ORIGINAL,
            passenger_name="Aisha Mohamed",
            ticket_number="1761234567890",
            pnr="ABC123",
            airline="Afriqiyah Airways",
            route_from="CAI",
            route_to="TIP",
            departure_date=date(2026, 7, 1),
            amount="450.00",
            currency="USD",
            supplier=self.supplier,
            ticket_status=TicketStatus.DRAFT,
        )
