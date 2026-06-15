from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from apps.ai_extraction.models import AiExtractionCorrection, DocumentExtractionJob, DocumentType
from apps.audit_logs.models import AuditLog
from apps.billing_confirmations.models import BillingConfirmationStatus, FinanceStatus, TravelBillingConfirmationNote
from apps.billing_confirmations.services import generate_tbcn, mark_finance_accepted
from apps.common.models import ControlSequence
from apps.common.services.sequences import generate_sequence
from apps.invoice_matching.services import match_invoice, match_invoice_line
from apps.master_data.models import Country, Department, Employee, Project, Supplier
from apps.permits.models import Permit, PermitType
from apps.supplier_invoices.models import MatchStatus, SupplierInvoice, SupplierInvoiceLine, SupplierInvoiceStatus
from apps.supplier_invoices.services import approve_supplier_invoice, create_supplier_invoice
from apps.ticket_versions.models import TicketAction, TicketStatus, TicketVersion
from apps.ticket_versions.services import confirm_ticket_version, create_ticket_version_from_confirmed_data, mark_ticket_cancelled
from apps.travel_cases.models import AccountType, TravelCase, TravelCaseStatus, TravelPurpose
from apps.travel_cases.services import assign_travel_case, create_travel_case, submit_travel_case


class BackendFoundationTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="tester", password="test")
        self.country = Country.objects.create(code="LY", name="Libya")
        self.department = Department.objects.create(code="OPS", name="Operations")
        self.project = Project.objects.create(code="SIRTE", name="Sirte Field Ops", country=self.country, cost_center="CC-001")
        self.supplier = Supplier.objects.create(code="AFR", name="Afriqiyah Airways")
        self.employee = Employee.objects.create(
            badge_number="EMP-2207",
            full_name="Omar A. El-Mabrouk",
            project=self.project,
            department=self.department,
            job_title="Field Engineer",
        )
        self.travel_case = TravelCase.objects.create(
            case_number="TRV-LY-2026-000001",
            employee=self.employee,
            badge_number=self.employee.badge_number,
            employee_name=self.employee.full_name,
            project=self.project,
            department=self.department,
            country=self.country,
            travel_purpose=TravelPurpose.BUSINESS_TRIP,
            account_type=AccountType.COMPANY,
            route_from="TIP",
            route_to="IST",
            requested_travel_date=date(2026, 6, 24),
            created_by=self.user,
        )
        self.ticket_version = TicketVersion.objects.create(
            travel_case=self.travel_case,
            version_number="V1",
            ticket_action=TicketAction.ORIGINAL,
            passenger_name=self.employee.full_name,
            ticket_number="074-2456891220",
            pnr="KQ7X4M",
            airline="Afriqiyah Airways",
            route_from="TIP",
            route_to="IST",
            departure_date=date(2026, 6, 24),
            amount=Decimal("1180.00"),
            supplier=self.supplier,
        )
        self.invoice = SupplierInvoice.objects.create(
            invoice_record_number="SIR-LY-2026-000001",
            supplier=self.supplier,
            supplier_invoice_number="INV-AFR-5521",
            invoice_date=date(2026, 6, 12),
            received_date=date(2026, 6, 14),
            total_amount=Decimal("1180.00"),
            created_by=self.user,
        )

    def test_control_sequence_unique_code_year(self):
        ControlSequence.objects.create(code="TRV-LY", year=2026, last_number=1)

        with self.assertRaises(IntegrityError), transaction.atomic():
            ControlSequence.objects.create(code="TRV-LY", year=2026, last_number=2)

    def test_generate_sequence_creates_sequential_numbers(self):
        first = generate_sequence("TRV-LY", 2026, "{code}-{year}-{number:06d}")
        second = generate_sequence("TRV-LY", 2026, "{code}-{year}-{number:06d}")

        self.assertEqual(first, "TRV-LY-2026-000001")
        self.assertEqual(second, "TRV-LY-2026-000002")

    def test_supplier_invoice_blocks_duplicate_supplier_invoice_number(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            SupplierInvoice.objects.create(
                invoice_record_number="SIR-LY-2026-000002",
                supplier=self.supplier,
                supplier_invoice_number="INV-AFR-5521",
                invoice_date=date(2026, 6, 13),
                received_date=date(2026, 6, 15),
                total_amount=Decimal("200.00"),
            )

    def test_ticket_version_blocks_duplicate_case_version_number(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            TicketVersion.objects.create(
                travel_case=self.travel_case,
                version_number="V1",
                ticket_action=TicketAction.REISSUE,
                passenger_name=self.employee.full_name,
                ticket_number="074-2456891999",
                pnr="KQ7X4M",
                route_from="TIP",
                route_to="IST",
                departure_date=date(2026, 6, 25),
                amount=Decimal("1200.00"),
                supplier=self.supplier,
            )

    def test_permit_blocks_duplicate_travel_case_permit_type(self):
        Permit.objects.create(travel_case=self.travel_case, permit_type=PermitType.LIBYA_PERMIT, created_by=self.user)

        with self.assertRaises(IntegrityError), transaction.atomic():
            Permit.objects.create(travel_case=self.travel_case, permit_type=PermitType.LIBYA_PERMIT, created_by=self.user)

    @override_settings(DEBUG=True)
    def test_seed_demo_data_is_idempotent(self):
        call_command("seed_demo_data", verbosity=0)
        call_command("seed_demo_data", verbosity=0)

        self.assertEqual(Employee.objects.filter(badge_number__startswith="DEMO-EMP-").count(), 3)
        self.assertEqual(TravelCase.objects.filter(notes__contains="seed_demo_data").count(), 3)
        self.assertEqual(Project.objects.filter(code__in=["DEMO-SIRTE", "DEMO-TRIPOLI"]).count(), 2)

    def test_tbcn_confirmation_no_is_unique(self):
        TravelBillingConfirmationNote.objects.create(
            confirmation_no="TBCN-LY-2026-000001",
            supplier_invoice=self.invoice,
            supplier=self.supplier,
            supplier_invoice_number=self.invoice.supplier_invoice_number,
            total_amount=Decimal("1180.00"),
            matched_amount=Decimal("1180.00"),
            status=BillingConfirmationStatus.GENERATED,
            generated_by=self.user,
        )
        second_invoice = SupplierInvoice.objects.create(
            invoice_record_number="SIR-LY-2026-000002",
            supplier=self.supplier,
            supplier_invoice_number="INV-AFR-5522",
            invoice_date=date(2026, 6, 13),
            received_date=date(2026, 6, 15),
            total_amount=Decimal("500.00"),
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            TravelBillingConfirmationNote.objects.create(
                confirmation_no="TBCN-LY-2026-000001",
                supplier_invoice=second_invoice,
                supplier=self.supplier,
                supplier_invoice_number=second_invoice.supplier_invoice_number,
                total_amount=Decimal("500.00"),
                matched_amount=Decimal("500.00"),
                status=BillingConfirmationStatus.GENERATED,
            )

    def test_ai_extraction_correction_stores_original_and_corrected_values(self):
        job = DocumentExtractionJob.objects.create(
            document_type=DocumentType.FLIGHT_TICKET,
            source_file="ai-extraction/sample-ticket.pdf",
            created_by=self.user,
        )
        correction = AiExtractionCorrection.objects.create(
            extraction_job=job,
            field_name="ticket_amount",
            original_ai_value={"value": "USD 1,180.00"},
            corrected_value={"value": "USD 1,185.00"},
            corrected_by=self.user,
            correction_reason="Manual review corrected the fare.",
        )

        self.assertEqual(correction.original_ai_value["value"], "USD 1,180.00")
        self.assertEqual(correction.corrected_value["value"], "USD 1,185.00")


class ApiFoundationTests(TestCase):
    def setUp(self):
        BackendFoundationTests.setUp(self)
        auditor_group = Group.objects.create(name="Auditor")
        self.user.groups.add(auditor_group)
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.tbcn = TravelBillingConfirmationNote.objects.create(
            confirmation_no="TBCN-LY-2026-000001",
            supplier_invoice=self.invoice,
            supplier=self.supplier,
            supplier_invoice_number=self.invoice.supplier_invoice_number,
            total_amount=Decimal("1180.00"),
            matched_amount=Decimal("1180.00"),
            status=BillingConfirmationStatus.GENERATED,
            generated_by=self.user,
        )
        self.audit_log = AuditLog.objects.create(
            user=self.user,
            action="Travel Case Created",
            entity_type="TravelCase",
            entity_id=str(self.travel_case.id),
        )

    def test_master_data_list_endpoint(self):
        response = self.client.get("/api/v1/countries/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)

    def test_travel_cases_list_endpoint(self):
        response = self.client.get("/api/v1/travel-cases/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)

    def test_supplier_invoices_list_endpoint(self):
        response = self.client.get("/api/v1/supplier-invoices/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)

    def test_tbcn_list_endpoint(self):
        response = self.client.get("/api/v1/tbcn/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)

    def test_audit_logs_are_read_only(self):
        list_response = self.client.get("/api/v1/audit-logs/")
        create_response = self.client.post(
            "/api/v1/audit-logs/",
            {
                "action": "Unsafe Write",
                "entity_type": "TravelCase",
                "entity_id": str(self.travel_case.id),
            },
            format="json",
        )

        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(create_response.status_code, 403)

    def test_openapi_schema_generation_does_not_fail(self):
        response = self.client.get("/api/schema/")

        self.assertEqual(response.status_code, 200)
        self.assertIn("openapi", response.data)

class WorkflowServiceTests(BackendFoundationTests):
    def _new_case_without_tickets(self):
        return TravelCase.objects.create(
            case_number="TRV-LY-2026-000099",
            employee=self.employee,
            badge_number=self.employee.badge_number,
            employee_name=self.employee.full_name,
            project=self.project,
            department=self.department,
            country=self.country,
            travel_purpose=TravelPurpose.BUSINESS_TRIP,
            account_type=AccountType.COMPANY,
            route_from="TIP",
            route_to="CAI",
            requested_travel_date=date(2026, 7, 5),
        )

    def _ticket_data(self, ticket_number="074-2456892000", amount=Decimal("500.00")):
        return {
            "ticket_action": TicketAction.ORIGINAL,
            "passenger_name": self.employee.full_name,
            "ticket_number": ticket_number,
            "pnr": "PNR123",
            "airline": "Afriqiyah Airways",
            "route_from": "TIP",
            "route_to": "CAI",
            "departure_date": date(2026, 7, 5),
            "amount": amount,
            "supplier": self.supplier,
        }

    def _invoice_line(self, ticket_number=None, invoiced_amount=Decimal("1180.00")):
        return SupplierInvoiceLine.objects.create(
            supplier_invoice=self.invoice,
            ticket_number=ticket_number or self.ticket_version.ticket_number,
            route_from="TIP",
            route_to="IST",
            invoiced_amount=invoiced_amount,
            account_type=AccountType.COMPANY,
        )

    def _approved_invoice_with_matched_line(self):
        line = self._invoice_line()
        match_invoice_line(line, self.user)
        self.invoice.status = SupplierInvoiceStatus.HR_APPROVED
        self.invoice.save(update_fields=["status", "updated_at"])
        return line

    def test_creating_travel_case_generates_trv_number(self):
        ControlSequence.objects.create(code="TRV-LY", year=2026, last_number=1)
        travel_case = create_travel_case(
            {
                "employee": self.employee,
                "project": self.project,
                "department": self.department,
                "country": self.country,
                "travel_purpose": TravelPurpose.BUSINESS_TRIP,
                "account_type": AccountType.COMPANY,
                "route_from": "TIP",
                "route_to": "TUN",
                "requested_travel_date": date(2026, 7, 1),
            },
            self.user,
        )

        self.assertEqual(travel_case.case_number, "TRV-LY-2026-000002")
        self.assertEqual(travel_case.employee_name, self.employee.full_name)

    def test_submitting_travel_case_changes_status_and_audits(self):
        submit_travel_case(self.travel_case, self.user)

        self.travel_case.refresh_from_db()
        self.assertEqual(self.travel_case.current_status, TravelCaseStatus.SUBMITTED_BY_HR)
        self.assertTrue(AuditLog.objects.filter(action="Travel Case Submitted").exists())

    def test_assigning_travel_case_sets_assignee_and_under_booking(self):
        assigned = get_user_model().objects.create_user(username="booking")
        submit_travel_case(self.travel_case, self.user)
        assign_travel_case(self.travel_case, assigned, self.user)

        self.travel_case.refresh_from_db()
        self.assertEqual(self.travel_case.assigned_to, assigned)
        self.assertEqual(self.travel_case.current_status, TravelCaseStatus.UNDER_BOOKING)

    def test_creating_first_ticket_version_uses_v1(self):
        travel_case = self._new_case_without_tickets()
        ticket = create_ticket_version_from_confirmed_data(travel_case, self._ticket_data(), self.user)

        self.assertEqual(ticket.version_number, "V1")

    def test_creating_second_ticket_version_uses_v2(self):
        ticket = create_ticket_version_from_confirmed_data(self.travel_case, self._ticket_data(), self.user)

        self.assertEqual(ticket.version_number, "V2")

    def test_confirming_ticket_version_sets_user_and_timestamp(self):
        confirm_ticket_version(self.ticket_version, self.user)

        self.ticket_version.refresh_from_db()
        self.assertEqual(self.ticket_version.confirmed_by, self.user)
        self.assertIsNotNone(self.ticket_version.confirmed_at)
        self.assertEqual(self.ticket_version.ticket_status, TicketStatus.ACTIVE)

    def test_cancelling_ticket_version_requires_reason(self):
        with self.assertRaises(ValidationError):
            mark_ticket_cancelled(self.ticket_version, "", self.user)

    def test_creating_supplier_invoice_generates_sir_number(self):
        ControlSequence.objects.create(code="SIR-LY", year=2026, last_number=1)
        invoice = create_supplier_invoice(
            {
                "supplier": self.supplier,
                "supplier_invoice_number": "INV-AFR-6000",
                "invoice_date": date(2026, 8, 1),
                "received_date": date(2026, 8, 2),
                "total_amount": Decimal("500.00"),
            },
            self.user,
        )

        self.assertEqual(invoice.invoice_record_number, "SIR-LY-2026-000002")

    def test_duplicate_supplier_invoice_remains_blocked(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            create_supplier_invoice(
                {
                    "supplier": self.supplier,
                    "supplier_invoice_number": self.invoice.supplier_invoice_number,
                    "invoice_date": date(2026, 8, 1),
                    "received_date": date(2026, 8, 2),
                    "total_amount": Decimal("500.00"),
                },
                self.user,
            )

    def test_invoice_matching_links_exact_ticket_number(self):
        line = self._invoice_line()
        match_invoice_line(line, self.user)

        line.refresh_from_db()
        self.assertEqual(line.ticket_version, self.ticket_version)
        self.assertEqual(line.travel_case, self.travel_case)
        self.assertEqual(line.match_status, MatchStatus.MATCHED)

    def test_invoice_matching_flags_amount_difference(self):
        line = self._invoice_line(invoiced_amount=Decimal("1200.00"))
        match_invoice_line(line, self.user)

        line.refresh_from_db()
        self.assertEqual(line.match_status, MatchStatus.DIFFERENCE)
        self.assertEqual(line.difference_amount, Decimal("20.00"))

    def test_invoice_matching_flags_missing_ticket_number(self):
        line = self._invoice_line(ticket_number="MISSING")
        match_invoice_line(line, self.user)

        line.refresh_from_db()
        self.assertEqual(line.match_status, MatchStatus.EXCEPTION)
        self.assertEqual(line.exception_reason, "Ticket number not found")

    def test_supplier_invoice_cannot_be_approved_without_lines(self):
        with self.assertRaises(ValidationError):
            approve_supplier_invoice(self.invoice, self.user)

    def test_supplier_invoice_can_be_approved_when_lines_matched(self):
        self._invoice_line()
        match_invoice(self.invoice, self.user)
        approve_supplier_invoice(self.invoice, self.user)

        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, SupplierInvoiceStatus.HR_APPROVED)

    def test_tbcn_generation_creates_tbcn_number(self):
        self._approved_invoice_with_matched_line()
        tbcn = generate_tbcn(self.invoice, self.user)

        self.assertTrue(tbcn.confirmation_no.startswith("TBCN-LY-2026-"))
        self.assertEqual(tbcn.status, BillingConfirmationStatus.GENERATED)

    def test_tbcn_generation_locks_invoice_lines_and_ticket_versions(self):
        line = self._approved_invoice_with_matched_line()
        generate_tbcn(self.invoice, self.user)

        self.invoice.refresh_from_db()
        line.refresh_from_db()
        self.ticket_version.refresh_from_db()
        self.assertTrue(self.invoice.is_locked)
        self.assertTrue(line.is_locked)
        self.assertTrue(self.ticket_version.is_locked)

    def test_cannot_generate_duplicate_tbcn_for_same_invoice(self):
        self._approved_invoice_with_matched_line()
        generate_tbcn(self.invoice, self.user)
        self.invoice.status = SupplierInvoiceStatus.HR_APPROVED
        self.invoice.is_locked = False
        self.invoice.save(update_fields=["status", "is_locked", "updated_at"])

        with self.assertRaises(ValidationError):
            generate_tbcn(self.invoice, self.user)

    def test_cannot_mark_finance_accepted_without_generated_tbcn(self):
        tbcn = TravelBillingConfirmationNote.objects.create(
            confirmation_no="TBCN-LY-2026-000099",
            supplier_invoice=self.invoice,
            supplier=self.supplier,
            supplier_invoice_number=self.invoice.supplier_invoice_number,
            total_amount=Decimal("1180.00"),
            matched_amount=Decimal("1180.00"),
            status=BillingConfirmationStatus.DRAFT,
            finance_status=FinanceStatus.NOT_SENT,
        )

        with self.assertRaises(ValidationError):
            mark_finance_accepted(tbcn, self.user)

    def test_audit_logs_created_for_core_workflow_actions(self):
        submit_travel_case(self.travel_case, self.user)
        confirm_ticket_version(self.ticket_version, self.user)
        line = self._invoice_line()
        match_invoice_line(line, self.user)

        self.assertTrue(AuditLog.objects.filter(action="Travel Case Submitted").exists())
        self.assertTrue(AuditLog.objects.filter(action="Ticket Confirmed").exists())
        self.assertTrue(AuditLog.objects.filter(action="Invoice Line Matched").exists())


# Create your tests here.
