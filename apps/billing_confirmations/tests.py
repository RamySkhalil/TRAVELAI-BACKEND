from datetime import date
from decimal import Decimal
import shutil
import tempfile

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.storage.filesystem import FileSystemStorage
from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.test import APIClient

from apps.audit_logs.models import AuditLog
from apps.billing_confirmations.models import BillingConfirmationStatus, FinanceStatus, TravelBillingConfirmationNote
from apps.master_data.models import Country, Department, Employee, Project, Supplier
from apps.supplier_invoices.models import MatchStatus, SupplierInvoice, SupplierInvoiceLine, SupplierInvoiceStatus
from apps.supplier_invoices.services import is_supplier_invoice_finance_ready
from apps.ticket_versions.models import TicketAction, TicketVersion
from apps.travel_cases.models import AccountType, TravelCase, TravelPurpose
from config.settings import CLOUDFLARE_R2_REQUIRED_ENV_NAMES, r2_storage_enabled


class TravelBillingConfirmationPhase11Tests(TestCase):
    def setUp(self):
        self.temp_media = tempfile.mkdtemp()
        self.storage_override = override_settings(
            MEDIA_ROOT=self.temp_media,
            STORAGES={
                "default": {
                    "BACKEND": "django.core.files.storage.FileSystemStorage",
                },
                "staticfiles": {
                    "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
                },
            },
        )
        self.storage_override.enable()
        self.addCleanup(self.storage_override.disable)
        self.addCleanup(shutil.rmtree, self.temp_media, ignore_errors=True)
        self.client = APIClient()
        self.user_model = get_user_model()
        self.finance_user = self._create_user("finance-user", "Finance")
        self.client.force_authenticate(self.finance_user)
        self.country = Country.objects.create(code="LY", name="Libya")
        self.department = Department.objects.create(code="OPS", name="Operations")
        self.project = Project.objects.create(code="TRIP", name="Tripoli Ops", country=self.country)
        self.supplier = Supplier.objects.create(code="AFR", name="Afriqiyah Airways")
        self.employee = Employee.objects.create(
            badge_number="E001",
            full_name="Aisha Mohamed",
            project=self.project,
            department=self.department,
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
            route_from="CAI",
            route_to="TIP",
            requested_travel_date=date(2026, 7, 1),
            created_by=self.finance_user,
        )
        self.ticket_version = TicketVersion.objects.create(
            travel_case=self.travel_case,
            version_number="V1",
            ticket_action=TicketAction.ORIGINAL,
            passenger_name=self.employee.full_name,
            ticket_number="1761234567890",
            pnr="ABC123",
            airline="Afriqiyah Airways",
            route_from="CAI",
            route_to="TIP",
            departure_date=date(2026, 7, 1),
            amount=Decimal("450.00"),
            currency="USD",
            supplier=self.supplier,
        )

    def test_cannot_generate_tbcn_unless_invoice_is_hr_approved(self):
        invoice = self._create_invoice(status_value=SupplierInvoiceStatus.MATCHED)
        self._create_line(invoice)

        response = self.client.post("/api/v1/tbcn/generate-tbcn/", {"supplier_invoice": invoice.id}, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(TravelBillingConfirmationNote.objects.count(), 0)

    def test_generates_tbcn_number(self):
        invoice = self._create_invoice()
        self._create_line(invoice)

        response = self.client.post("/api/v1/tbcn/generate-tbcn/", {"supplier_invoice": invoice.id}, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(response.data["confirmation_no"].startswith("TBCN-LY-2026-"))
        self.assertEqual(response.data["confirmation_no"], "TBCN-LY-2026-000001")
        self.assertEqual(response.data["status"], BillingConfirmationStatus.GENERATED)
        self.assertEqual(response.data["finance_status"], FinanceStatus.NOT_SENT)

    def test_tbcn_locks_invoice_lines_and_linked_ticket_versions(self):
        invoice = self._create_invoice()
        line = self._create_line(invoice)

        response = self.client.post("/api/v1/tbcn/generate-tbcn/", {"supplier_invoice": invoice.id}, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        invoice.refresh_from_db()
        line.refresh_from_db()
        self.ticket_version.refresh_from_db()
        self.assertEqual(invoice.status, SupplierInvoiceStatus.TBCN_GENERATED)
        self.assertTrue(invoice.is_locked)
        self.assertTrue(line.is_locked)
        self.assertTrue(self.ticket_version.is_locked)
        self.assertTrue(AuditLog.objects.filter(action="TBCN Generated", entity_id=response.data["id"]).exists())

    def test_duplicate_tbcn_generation_is_blocked(self):
        invoice = self._create_invoice()
        self._create_line(invoice)
        first = self.client.post("/api/v1/tbcn/generate-tbcn/", {"supplier_invoice": invoice.id}, format="json")

        response = self.client.post("/api/v1/tbcn/generate-tbcn/", {"supplier_invoice": invoice.id}, format="json")

        self.assertEqual(first.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(TravelBillingConfirmationNote.objects.count(), 1)

    def test_send_to_finance_changes_status_and_finance_status(self):
        tbcn = self._generate_tbcn()

        response = self.client.post(f"/api/v1/tbcn/{tbcn.id}/send-to-finance/", {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], BillingConfirmationStatus.SENT_TO_FINANCE)
        self.assertEqual(response.data["finance_status"], FinanceStatus.SENT)
        self.assertEqual(response.data["sent_to_finance_by"], self.finance_user.id)
        self.assertIsNotNone(response.data["sent_to_finance_at"])

    def test_finance_accepted_changes_status_and_finance_status(self):
        tbcn = self._generate_tbcn()
        self.client.post(f"/api/v1/tbcn/{tbcn.id}/send-to-finance/", {}, format="json")

        response = self.client.post(f"/api/v1/tbcn/{tbcn.id}/mark-finance-accepted/", {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], BillingConfirmationStatus.FINANCE_ACCEPTED)
        self.assertEqual(response.data["finance_status"], FinanceStatus.ACCEPTED)

    def test_mark_paid_changes_status_and_finance_status(self):
        tbcn = self._generate_tbcn()
        self.client.post(f"/api/v1/tbcn/{tbcn.id}/send-to-finance/", {}, format="json")
        self.client.post(f"/api/v1/tbcn/{tbcn.id}/mark-finance-accepted/", {}, format="json")

        response = self.client.post(f"/api/v1/tbcn/{tbcn.id}/mark-paid/", {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], BillingConfirmationStatus.PAID)
        self.assertEqual(response.data["finance_status"], FinanceStatus.PAID)

    def test_cannot_mark_paid_without_sent_and_accepted_tbcn(self):
        tbcn = self._generate_tbcn()

        response = self.client.post(f"/api/v1/tbcn/{tbcn.id}/mark-paid/", {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        tbcn.refresh_from_db()
        self.assertEqual(tbcn.status, BillingConfirmationStatus.GENERATED)
        self.assertEqual(tbcn.finance_status, FinanceStatus.NOT_SENT)

    def test_draft_tbcn_cannot_be_marked_paid(self):
        invoice = self._create_invoice()
        tbcn = TravelBillingConfirmationNote.objects.create(
            confirmation_no="TBCN-LY-2026-009999",
            supplier_invoice=invoice,
            supplier=self.supplier,
            supplier_invoice_number=invoice.supplier_invoice_number,
            total_amount=invoice.total_amount,
            matched_amount=Decimal("0.00"),
            difference_amount=Decimal("0.00"),
            currency=invoice.currency,
            status=BillingConfirmationStatus.DRAFT,
        )

        response = self.client.post(f"/api/v1/tbcn/{tbcn.id}/mark-paid/", {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_supplier_invoice_is_not_finance_ready_without_tbcn(self):
        invoice = self._create_invoice()

        self.assertFalse(is_supplier_invoice_finance_ready(invoice))

    def test_supplier_invoice_is_finance_ready_after_tbcn_generation(self):
        invoice = self._create_invoice()
        self._create_line(invoice)
        self.client.post("/api/v1/tbcn/generate-tbcn/", {"supplier_invoice": invoice.id}, format="json")
        invoice.refresh_from_db()

        self.assertTrue(is_supplier_invoice_finance_ready(invoice))

    def test_r2_settings_helper_returns_disabled_when_env_incomplete(self):
        env = {name: "configured" for name in CLOUDFLARE_R2_REQUIRED_ENV_NAMES[:-1]}

        self.assertFalse(r2_storage_enabled(env))

    def test_r2_settings_helper_returns_enabled_when_required_env_names_exist(self):
        env = {name: "configured" for name in CLOUDFLARE_R2_REQUIRED_ENV_NAMES}

        self.assertTrue(r2_storage_enabled(env))

    def test_cannot_generate_pdf_for_missing_tbcn(self):
        response = self.client.post("/api/v1/tbcn/999999/generate-pdf/", {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_can_generate_pdf_for_generated_tbcn(self):
        tbcn = self._generate_tbcn()

        response = self.client.post(f"/api/v1/tbcn/{tbcn.id}/generate-pdf/", {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        tbcn.refresh_from_db()
        self.assertTrue(tbcn.pdf_file)
        self.assertTrue(tbcn.pdf_file.name.endswith(".pdf"))
        self.assertEqual(response.data["has_pdf"], True)
        self.assertTrue(response.data["pdf_url"])
        self.assertIsNotNone(response.data["pdf_generated_at"])

    def test_pdf_generation_does_not_change_finance_status(self):
        tbcn = self._generate_tbcn()
        original_status = tbcn.status
        original_finance_status = tbcn.finance_status

        response = self.client.post(f"/api/v1/tbcn/{tbcn.id}/generate-pdf/", {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        tbcn.refresh_from_db()
        self.assertEqual(tbcn.status, original_status)
        self.assertEqual(tbcn.finance_status, original_finance_status)

    def test_pdf_generation_creates_audit_log(self):
        tbcn = self._generate_tbcn()

        response = self.client.post(f"/api/v1/tbcn/{tbcn.id}/generate-pdf/", {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(AuditLog.objects.filter(action="TBCN PDF Generated", entity_id=tbcn.id).exists())

    def test_cannot_generate_pdf_for_draft_tbcn(self):
        invoice = self._create_invoice()
        tbcn = TravelBillingConfirmationNote.objects.create(
            confirmation_no="TBCN-LY-2026-008888",
            supplier_invoice=invoice,
            supplier=self.supplier,
            supplier_invoice_number=invoice.supplier_invoice_number,
            total_amount=invoice.total_amount,
            matched_amount=Decimal("0.00"),
            difference_amount=Decimal("0.00"),
            currency=invoice.currency,
            status=BillingConfirmationStatus.DRAFT,
        )

        response = self.client.post(f"/api/v1/tbcn/{tbcn.id}/generate-pdf/", {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        tbcn.refresh_from_db()
        self.assertFalse(tbcn.pdf_file)

    def test_serializer_exposes_pdf_availability_safely(self):
        tbcn = self._generate_tbcn()

        detail_response = self.client.get(f"/api/v1/tbcn/{tbcn.id}/")
        self.assertEqual(detail_response.status_code, status.HTTP_200_OK)
        self.assertFalse(detail_response.data["has_pdf"])
        self.assertEqual(detail_response.data["pdf_url"], "")

        self.client.post(f"/api/v1/tbcn/{tbcn.id}/generate-pdf/", {}, format="json")
        detail_response = self.client.get(f"/api/v1/tbcn/{tbcn.id}/")

        self.assertEqual(detail_response.status_code, status.HTTP_200_OK)
        self.assertTrue(detail_response.data["has_pdf"])
        self.assertNotIn("CLOUDFLARE_R2_SECRET_ACCESS_KEY", str(detail_response.data))

    def test_finance_report_lists_tbcn_records(self):
        tbcn = self._generate_tbcn()

        response = self.client.get("/api/v1/finance-control-report/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["confirmation_no"], tbcn.confirmation_no)

    def test_finance_report_filters_by_finance_status(self):
        paid_tbcn = self._generate_tbcn()
        self.client.post(f"/api/v1/tbcn/{paid_tbcn.id}/send-to-finance/", {}, format="json")
        self.client.post(f"/api/v1/tbcn/{paid_tbcn.id}/mark-finance-accepted/", {}, format="json")
        self.client.post(f"/api/v1/tbcn/{paid_tbcn.id}/mark-paid/", {}, format="json")
        self._generate_tbcn()

        response = self.client.get("/api/v1/finance-control-report/", {"finance_status": FinanceStatus.PAID})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["finance_status"], FinanceStatus.PAID)

    def test_finance_report_csv_export_returns_expected_columns(self):
        self._generate_tbcn()

        response = self.client.get("/api/v1/finance-control-report/export-csv/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response["Content-Type"], "text/csv")
        content = response.content.decode("utf-8")
        self.assertIn("TBCN number,Supplier,Supplier invoice number,Total amount", content)
        self.assertIn("PDF available", content)

    def test_pdf_generation_uses_local_storage_fallback_when_configured(self):
        tbcn = self._generate_tbcn()

        response = self.client.post(f"/api/v1/tbcn/{tbcn.id}/generate-pdf/", {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        tbcn.refresh_from_db()
        self.assertIsInstance(tbcn.pdf_file.storage, FileSystemStorage)

    def test_r2_enabled_detection_does_not_expose_secret_values(self):
        env = {name: f"secret-{index}" for index, name in enumerate(CLOUDFLARE_R2_REQUIRED_ENV_NAMES)}

        self.assertTrue(r2_storage_enabled(env))
        self.assertNotIn("secret-", ",".join(CLOUDFLARE_R2_REQUIRED_ENV_NAMES))

    def _create_user(self, username, group_name):
        user = self.user_model.objects.create_user(username=username, password="test-pass")
        group, _ = Group.objects.get_or_create(name=group_name)
        user.groups.add(group)
        return user

    def _create_invoice(self, status_value=SupplierInvoiceStatus.HR_APPROVED):
        return SupplierInvoice.objects.create(
            invoice_record_number=f"SIR-LY-2026-{SupplierInvoice.objects.count() + 1:06d}",
            supplier=self.supplier,
            supplier_invoice_number=f"INV-AFR-{SupplierInvoice.objects.count() + 8000}",
            invoice_date=date(2026, 7, 5),
            received_date=date(2026, 7, 6),
            currency="USD",
            total_amount=Decimal("450.00"),
            status=status_value,
            created_by=self.finance_user,
            approved_by=self.finance_user if status_value == SupplierInvoiceStatus.HR_APPROVED else None,
        )

    def _create_line(self, invoice):
        return SupplierInvoiceLine.objects.create(
            supplier_invoice=invoice,
            travel_case=self.travel_case,
            ticket_version=self.ticket_version,
            employee=self.employee,
            ticket_number=self.ticket_version.ticket_number,
            route_from="CAI",
            route_to="TIP",
            booked_amount=Decimal("450.00"),
            invoiced_amount=Decimal("450.00"),
            difference_amount=Decimal("0.00"),
            account_type=AccountType.COMPANY,
            match_status=MatchStatus.MATCHED,
        )

    def _generate_tbcn(self):
        invoice = self._create_invoice()
        self._create_line(invoice)
        response = self.client.post("/api/v1/tbcn/generate-tbcn/", {"supplier_invoice": invoice.id}, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        return TravelBillingConfirmationNote.objects.get(id=response.data["id"])
