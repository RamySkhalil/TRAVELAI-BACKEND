"""Phase 14 tests: health/readiness, environment validation, demo seed, and the
end-to-end controlled workflow chain at service/API level.

All records are created inside the test transaction. No production or Supabase
data is used.
"""
import os
import shutil
import tempfile
from datetime import date
from decimal import Decimal
from io import StringIO
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from apps.ai_extraction.services import confirm_extraction_job, create_extraction_job, run_invoice_extraction, run_ticket_extraction
from apps.audit_logs.models import AuditLog
from apps.billing_confirmations.models import BillingConfirmationStatus, FinanceStatus, TravelBillingConfirmationNote
from apps.billing_confirmations.services import (
    generate_tbcn,
    generate_tbcn_pdf,
    mark_finance_accepted,
    mark_tbcn_paid,
    send_tbcn_to_finance,
)
from apps.dashboard.services import dashboard_summary, pending_actions_for_user
from apps.invoice_matching.services import match_invoice
from apps.master_data.models import Country, Department, Employee, Project, Supplier
from apps.supplier_invoices.models import SupplierInvoice, SupplierInvoiceStatus
from apps.supplier_invoices.services import approve_supplier_invoice, create_supplier_invoice_from_confirmed_extraction, is_supplier_invoice_finance_ready
from apps.ticket_versions.models import TicketAction, TicketStatus
from apps.ticket_versions.services import confirm_ticket_version, create_ticket_version_from_confirmed_extraction
from apps.travel_cases.models import TravelCase, TravelCaseStatus
from apps.travel_cases.services import assign_travel_case, submit_travel_case


def _temp_storage_override():
    temp_media = tempfile.mkdtemp()
    override = override_settings(
        MEDIA_ROOT=temp_media,
        STORAGES={
            "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
            "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
        },
    )
    return temp_media, override


class HealthEndpointTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_health_endpoint_returns_ok_without_authentication(self):
        response = self.client.get("/api/health/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "ok")
        self.assertIn("version", response.data)

    def test_readiness_requires_staff(self):
        user = get_user_model().objects.create_user(username="plain-user", password="test-pass")
        self.client.force_authenticate(user)

        response = self.client.get("/api/readiness/")

        self.assertIn(response.status_code, [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN])

    def test_readiness_reports_database_ok_for_staff(self):
        staff = get_user_model().objects.create_user(username="staff-user", password="test-pass", is_staff=True)
        self.client.force_authenticate(staff)

        response = self.client.get("/api/readiness/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["database"], "ok")
        self.assertIn("storage", response.data)
        self.assertIn("r2_enabled", response.data["storage"])

    def test_readiness_does_not_expose_secret_values(self):
        staff = get_user_model().objects.create_user(username="staff-user-2", password="test-pass", is_staff=True)
        self.client.force_authenticate(staff)

        with override_settings(SECRET_KEY="phase14-secret-marker-should-not-leak"):
            response = self.client.get("/api/readiness/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertNotIn("phase14-secret-marker-should-not-leak", str(response.data))


class CheckEnvReadyCommandTests(TestCase):
    def test_command_runs_and_reports_flags(self):
        out = StringIO()
        call_command("check_env_ready", stdout=out)
        output = out.getvalue()

        self.assertIn("Environment readiness report", output)
        self.assertIn("Database connectivity", output)

    def test_command_does_not_print_secret_values(self):
        out = StringIO()
        fake_env = {
            "OPENAI_API_KEY": "openai-secret-do-not-print-123",
            "CLOUDFLARE_R2_SECRET_ACCESS_KEY": "r2-secret-do-not-print-456",
            "DATABASE_URL": "postgres://user:supersecretpw@host:5432/db",
        }
        with mock.patch.dict(os.environ, fake_env, clear=False):
            with override_settings(SECRET_KEY="django-secret-do-not-print-789"):
                call_command("check_env_ready", stdout=out)
        output = out.getvalue()

        self.assertNotIn("openai-secret-do-not-print-123", output)
        self.assertNotIn("r2-secret-do-not-print-456", output)
        self.assertNotIn("supersecretpw", output)
        self.assertNotIn("django-secret-do-not-print-789", output)


class SeedFullDemoScenarioTests(TestCase):
    def setUp(self):
        self.temp_media, self.storage_override = _temp_storage_override()
        self.storage_override.enable()
        self.addCleanup(self.storage_override.disable)
        self.addCleanup(shutil.rmtree, self.temp_media, ignore_errors=True)

    @override_settings(DEBUG=True)
    def test_seed_full_demo_scenario_is_idempotent(self):
        call_command("seed_full_demo_scenario", verbosity=0)
        case_count = TravelCase.objects.filter(notes__contains="seed_full_demo_scenario").count()
        invoice_count = SupplierInvoice.objects.count()
        tbcn_count = TravelBillingConfirmationNote.objects.count()

        call_command("seed_full_demo_scenario", verbosity=0)

        self.assertEqual(TravelCase.objects.filter(notes__contains="seed_full_demo_scenario").count(), case_count)
        self.assertEqual(SupplierInvoice.objects.count(), invoice_count)
        self.assertEqual(TravelBillingConfirmationNote.objects.count(), tbcn_count)
        self.assertGreaterEqual(case_count, 7)

    @override_settings(DEBUG=True)
    def test_seed_full_demo_scenario_creates_full_chain(self):
        call_command("seed_full_demo_scenario", verbosity=0)

        self.assertTrue(TravelCase.objects.filter(current_status=TravelCaseStatus.SUBMITTED_BY_HR).exists())
        self.assertTrue(SupplierInvoice.objects.filter(status=SupplierInvoiceStatus.AWAITING_MATCHING).exists())
        self.assertTrue(SupplierInvoice.objects.filter(status=SupplierInvoiceStatus.HR_APPROVED).exists())
        self.assertTrue(TravelBillingConfirmationNote.objects.filter(finance_status=FinanceStatus.PAID).exists())
        self.assertTrue(TravelBillingConfirmationNote.objects.filter(finance_status=FinanceStatus.SENT).exists())
        self.assertTrue(SupplierInvoice.objects.filter(currency="USD").exists())
        self.assertTrue(SupplierInvoice.objects.filter(currency="EGP").exists())
        self.assertTrue(TravelBillingConfirmationNote.objects.filter(currency="EGP").exists())

    @override_settings(DEBUG=False)
    def test_seed_full_demo_scenario_refuses_when_not_debug(self):
        from django.core.management.base import CommandError

        with self.assertRaises(CommandError):
            call_command("seed_full_demo_scenario", verbosity=0)


class EndToEndWorkflowTests(TestCase):
    def setUp(self):
        self.temp_media, self.storage_override = _temp_storage_override()
        self.storage_override.enable()
        self.addCleanup(self.storage_override.disable)
        self.addCleanup(shutil.rmtree, self.temp_media, ignore_errors=True)

        self.user_model = get_user_model()
        Group.objects.bulk_create(
            [Group(name=name) for name in ("Admin", "HR", "BookingOfficer", "Finance")],
            ignore_conflicts=True,
        )
        self.hr_user = self._user("hr-e2e", "HR")
        self.booking_user = self._user("booking-e2e", "BookingOfficer")
        self.finance_user = self._user("finance-e2e", "Finance")
        self.admin_user = self._user("admin-e2e", "Admin", is_staff=True)

        self.country = Country.objects.create(code="E2E", name="End To End Test Country")
        self.department = Department.objects.create(code="E2E-OPS", name="End To End Operations")
        self.project = Project.objects.create(code="E2E-TRIP", name="End To End Tripoli Ops", country=self.country)
        # The mock ticket extraction returns this supplier name; it must resolve in master data.
        self.supplier = Supplier.objects.create(code="E2E-MTS", name="Mock Travel Supplier")
        self.employee = Employee.objects.create(
            badge_number="E2E-001",
            full_name="Aisha Mohamed",
            project=self.project,
            department=self.department,
        )

    def _user(self, username, group_name, is_staff=False):
        user = self.user_model.objects.create_user(username=username, password="test-pass", is_staff=is_staff)
        group = Group.objects.get(name=group_name)
        user.groups.add(group)
        return user

    def _create_submitted_case(self, route_to="TIP", travel_date=date(2026, 7, 1), notes=""):
        from apps.travel_cases.services import create_travel_case

        case = create_travel_case(
            {
                "employee": self.employee,
                "project": self.project,
                "department": self.department,
                "country": self.country,
                "travel_purpose": "BUSINESS_TRIP",
                "account_type": "COMPANY",
                "route_from": "CAI",
                "route_to": route_to,
                "requested_travel_date": travel_date,
                "notes": notes,
            },
            self.hr_user,
        )
        submit_travel_case(case, self.hr_user)
        return case

    def _confirmed_ticket_job(self):
        job = create_extraction_job(document_type=None, raw_text="flight ticket sample", user=self.booking_user)
        run_ticket_extraction(job, provider_name="mock", user=self.booking_user)
        return confirm_extraction_job(job, user=self.booking_user)

    def _confirmed_invoice_job(self):
        job = create_extraction_job(document_type=None, raw_text="supplier invoice sample", user=self.hr_user)
        run_invoice_extraction(job, provider_name="mock", user=self.hr_user)
        return confirm_extraction_job(job, user=self.hr_user)

    def test_full_controlled_workflow_completes_with_tbcn_and_pdf(self):
        # 1-2 create and submit travel case
        case = self._create_submitted_case()
        self.assertEqual(case.current_status, TravelCaseStatus.SUBMITTED_BY_HR)

        # 3 assign to booking officer
        assign_travel_case(case, self.booking_user, self.booking_user)
        case.refresh_from_db()
        self.assertEqual(case.current_status, TravelCaseStatus.UNDER_BOOKING)

        # 4-5 confirmed ticket extraction -> ticket version
        ticket_job = self._confirmed_ticket_job()
        ticket = create_ticket_version_from_confirmed_extraction(ticket_job, case, TicketAction.ORIGINAL, user=self.booking_user)
        self.assertEqual(ticket.version_number, "V1")

        # 6 confirm ticket version
        confirm_ticket_version(ticket, self.booking_user)
        ticket.refresh_from_db()
        self.assertEqual(ticket.ticket_status, TicketStatus.ACTIVE)

        # 7-8 confirmed invoice extraction -> supplier invoice + lines
        invoice_job = self._confirmed_invoice_job()
        invoice = create_supplier_invoice_from_confirmed_extraction(invoice_job, self.supplier, user=self.hr_user)
        self.assertEqual(invoice.lines.count(), 1)

        # 9 run matching
        match_invoice(invoice, self.hr_user)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, SupplierInvoiceStatus.MATCHED)

        # 10 approve
        approve_supplier_invoice(invoice, self.hr_user)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, SupplierInvoiceStatus.HR_APPROVED)

        # 11 generate TBCN
        tbcn = generate_tbcn(invoice, self.hr_user)
        self.assertTrue(tbcn.confirmation_no.startswith("TBCN-"))
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, SupplierInvoiceStatus.TBCN_GENERATED)
        self.assertTrue(is_supplier_invoice_finance_ready(invoice))

        # 12 generate PDF
        generate_tbcn_pdf(tbcn, self.hr_user)
        tbcn.refresh_from_db()
        self.assertTrue(tbcn.pdf_file)

        # 13 send to finance
        send_tbcn_to_finance(tbcn, self.finance_user)
        tbcn.refresh_from_db()
        self.assertEqual(tbcn.finance_status, FinanceStatus.SENT)

        # 14 finance accepted
        mark_finance_accepted(tbcn, self.finance_user)
        tbcn.refresh_from_db()
        self.assertEqual(tbcn.finance_status, FinanceStatus.ACCEPTED)

        # 15 paid
        mark_tbcn_paid(tbcn, self.finance_user)
        tbcn.refresh_from_db()
        self.assertEqual(tbcn.finance_status, FinanceStatus.PAID)

        # 16 audit logs for critical actions
        for action in [
            "Travel Case Created",
            "Travel Case Submitted",
            "Ticket Confirmed",
            "Supplier Invoice Created From Extraction",
            "Invoice Matching Run",
            "Supplier Invoice Approved",
            "TBCN Generated",
            "TBCN PDF Generated",
            "TBCN Sent To Finance",
            "TBCN Finance Accepted",
            "TBCN Paid",
        ]:
            self.assertTrue(AuditLog.objects.filter(action=action).exists(), f"Missing audit log: {action}")

        # 17 dashboard reflects records
        summary = dashboard_summary()
        self.assertEqual(summary["tbcn_finance"]["paid"], 1)
        self.assertEqual(summary["tickets"]["total"], 1)
        self.assertGreaterEqual(summary["supplier_invoices"]["total"], 1)

    def test_pending_actions_change_as_workflow_progresses(self):
        # A freshly submitted case should appear in the booking officer queue.
        case = self._create_submitted_case(notes="pending-flow")
        booking_pending = pending_actions_for_user(self.booking_user)
        self.assertTrue(
            any(item["type"] == "TRAVEL_CASE" and item["status"] == TravelCaseStatus.SUBMITTED_BY_HR for item in booking_pending["items"])
        )

        # Progress to a TBCN sent to finance and confirm finance sees acceptance work.
        assign_travel_case(case, self.booking_user, self.booking_user)
        ticket_job = self._confirmed_ticket_job()
        ticket = create_ticket_version_from_confirmed_extraction(ticket_job, case, TicketAction.ORIGINAL, user=self.booking_user)
        confirm_ticket_version(ticket, self.booking_user)
        invoice_job = self._confirmed_invoice_job()
        invoice = create_supplier_invoice_from_confirmed_extraction(invoice_job, self.supplier, user=self.hr_user)
        match_invoice(invoice, self.hr_user)
        approve_supplier_invoice(invoice, self.hr_user)
        tbcn = generate_tbcn(invoice, self.hr_user)
        send_tbcn_to_finance(tbcn, self.finance_user)

        finance_pending = pending_actions_for_user(self.finance_user)
        self.assertTrue(any(item["type"] == "TBCN" and item["status"] == FinanceStatus.SENT for item in finance_pending["items"]))

    def test_unauthorized_user_cannot_mutate_protected_workflow_endpoints(self):
        client = APIClient()  # no authentication

        responses = [
            client.post("/api/v1/travel-cases/", {"route_from": "CAI"}, format="json"),
            client.post("/api/v1/tbcn/generate-tbcn/", {"supplier_invoice": 1}, format="json"),
            client.post("/api/v1/tbcn/1/mark-paid/", {}, format="json"),
        ]
        for response in responses:
            self.assertIn(response.status_code, [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN])

    def test_finance_cannot_mark_paid_without_generated_tbcn(self):
        invoice = SupplierInvoice.objects.create(
            invoice_record_number="SIR-LY-2026-000900",
            supplier=self.supplier,
            supplier_invoice_number="INV-NO-TBCN-1",
            invoice_date=date(2026, 7, 5),
            received_date=date(2026, 7, 6),
            currency="USD",
            total_amount=Decimal("450.00"),
            status=SupplierInvoiceStatus.HR_APPROVED,
            created_by=self.hr_user,
        )
        # No TBCN exists, so the invoice is not finance-ready and cannot be paid.
        self.assertFalse(is_supplier_invoice_finance_ready(invoice))

        draft_tbcn = TravelBillingConfirmationNote.objects.create(
            confirmation_no="TBCN-LY-2026-009000",
            supplier_invoice=invoice,
            supplier=self.supplier,
            supplier_invoice_number=invoice.supplier_invoice_number,
            total_amount=invoice.total_amount,
            matched_amount=Decimal("0.00"),
            difference_amount=Decimal("0.00"),
            currency=invoice.currency,
            status=BillingConfirmationStatus.DRAFT,
            finance_status=FinanceStatus.NOT_SENT,
        )
        with self.assertRaises(ValidationError):
            mark_tbcn_paid(draft_tbcn, self.finance_user)
