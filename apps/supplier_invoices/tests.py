from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from apps.ai_extraction.models import DocumentExtractionJob, DocumentType, ExtractionStatus
from apps.audit_logs.models import AuditLog
from apps.billing_confirmations.models import TravelBillingConfirmationNote
from apps.master_data.models import Country, Department, Employee, Project, Supplier
from apps.supplier_invoices.models import MatchStatus, SupplierInvoice, SupplierInvoiceLine, SupplierInvoiceStatus
from apps.ticket_versions.models import TicketAction, TicketVersion
from apps.travel_cases.models import AccountType, TravelCase, TravelPurpose


class SupplierInvoicePhase10Tests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user_model = get_user_model()
        self.hr_user = self._create_user("hr-user", "HR")
        self.client.force_authenticate(self.hr_user)
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
            created_by=self.hr_user,
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

    def test_cannot_create_supplier_invoice_from_unconfirmed_extraction(self):
        job = self._create_extraction_job(status=ExtractionStatus.EXTRACTED)

        response = self.client.post(
            "/api/v1/supplier-invoices/create-from-extraction/",
            self._create_payload(job),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(SupplierInvoice.objects.count(), 0)
        self.assertEqual(SupplierInvoiceLine.objects.count(), 0)

    def test_confirmed_invoice_extraction_creates_supplier_invoice(self):
        job = self._create_extraction_job()

        response = self.client.post(
            "/api/v1/supplier-invoices/create-from-extraction/",
            self._create_payload(job),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        invoice = SupplierInvoice.objects.get()
        self.assertEqual(invoice.supplier, self.supplier)
        self.assertEqual(invoice.supplier_invoice_number, "INV-AFR-7000")
        self.assertEqual(invoice.status, SupplierInvoiceStatus.AWAITING_MATCHING)

    def test_supplier_invoice_gets_sir_number(self):
        job = self._create_extraction_job()

        response = self.client.post(
            "/api/v1/supplier-invoices/create-from-extraction/",
            self._create_payload(job),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["invoice_record_number"].startswith("SIR-LY-2026-"))

    def test_invoice_lines_are_created_from_extraction_lines(self):
        job = self._create_extraction_job()

        response = self.client.post(
            "/api/v1/supplier-invoices/create-from-extraction/",
            self._create_payload(job),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(SupplierInvoiceLine.objects.count(), 2)
        self.assertEqual(response.data["lines"][0]["ticket_number"], "1761234567890")

    def test_matching_links_line_to_ticket_version_by_ticket_number(self):
        invoice = self._create_invoice()
        line = self._create_line(invoice)

        response = self.client.post("/api/v1/invoice-matching/match-invoice/", {"supplier_invoice": invoice.id}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        line.refresh_from_db()
        self.assertEqual(line.ticket_version, self.ticket_version)
        self.assertEqual(line.travel_case, self.travel_case)
        self.assertEqual(line.match_status, MatchStatus.MATCHED)

    def test_amount_difference_becomes_difference(self):
        invoice = self._create_invoice(total_amount=Decimal("460.00"))
        line = self._create_line(invoice, invoiced_amount=Decimal("460.00"))

        response = self.client.post("/api/v1/invoice-matching/match-invoice/", {"supplier_invoice": invoice.id}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        line.refresh_from_db()
        self.assertEqual(line.match_status, MatchStatus.DIFFERENCE)
        self.assertEqual(line.difference_amount, Decimal("10.00"))

    def test_missing_ticket_becomes_exception(self):
        invoice = self._create_invoice()
        line = self._create_line(invoice, ticket_number="MISSING")

        response = self.client.post("/api/v1/invoice-matching/match-invoice/", {"supplier_invoice": invoice.id}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        line.refresh_from_db()
        self.assertEqual(line.match_status, MatchStatus.EXCEPTION)
        self.assertEqual(line.exception_reason, "Ticket number not found")

    def test_cannot_approve_invoice_without_lines(self):
        invoice = self._create_invoice()

        response = self.client.post(f"/api/v1/supplier-invoices/{invoice.id}/approve/", {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, SupplierInvoiceStatus.INVOICE_RECEIVED)

    def test_can_approve_invoice_after_matched_lines(self):
        invoice = self._create_invoice()
        self._create_line(invoice)
        self.client.post("/api/v1/invoice-matching/match-invoice/", {"supplier_invoice": invoice.id}, format="json")

        response = self.client.post(f"/api/v1/supplier-invoices/{invoice.id}/approve/", {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, SupplierInvoiceStatus.HR_APPROVED)

    def test_tbcn_is_not_generated_in_phase_10_approval(self):
        invoice = self._create_invoice()
        self._create_line(invoice)
        self.client.post("/api/v1/invoice-matching/match-invoice/", {"supplier_invoice": invoice.id}, format="json")

        response = self.client.post(f"/api/v1/supplier-invoices/{invoice.id}/approve/", {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(TravelBillingConfirmationNote.objects.count(), 0)

    def test_audit_logs_are_created(self):
        job = self._create_extraction_job()

        response = self.client.post(
            "/api/v1/supplier-invoices/create-from-extraction/",
            self._create_payload(job),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        invoice = SupplierInvoice.objects.get()
        self.assertTrue(AuditLog.objects.filter(action="Supplier Invoice Created", entity_id=str(invoice.id)).exists())
        self.assertTrue(AuditLog.objects.filter(action="Supplier Invoice Created From Extraction", entity_id=str(invoice.id)).exists())
        self.assertTrue(AuditLog.objects.filter(action="Supplier Invoice Line Created").exists())

    def _create_user(self, username, group_name):
        user = self.user_model.objects.create_user(username=username, password="test-pass")
        group, _ = Group.objects.get_or_create(name=group_name)
        user.groups.add(group)
        return user

    def _create_extraction_job(self, status=ExtractionStatus.CONFIRMED):
        return DocumentExtractionJob.objects.create(
            document_type=DocumentType.SUPPLIER_INVOICE,
            status=status,
            normalized_data={
                "document_type": "SUPPLIER_INVOICE",
                "supplier_name": "Afriqiyah Airways",
                "supplier_invoice_number": "INV-AFR-7000",
                "invoice_date": "2026-07-05",
                "received_date": "2026-07-06",
                "currency": "USD",
                "total_amount": "900.00",
                "lines": [
                    {
                        "passenger_name": "Aisha Mohamed",
                        "ticket_number": "1761234567890",
                        "route_from": "CAI",
                        "route_to": "TIP",
                        "amount": "450.00",
                        "currency": "USD",
                    },
                    {
                        "passenger_name": "Omar Hassan",
                        "ticket_number": "1761234567891",
                        "route_from": "TIP",
                        "route_to": "CAI",
                        "amount": "450.00",
                        "currency": "USD",
                    },
                ],
                "confidence": {"supplier_invoice_number": 0.98},
                "missing_critical_fields": [],
            },
            confidence_json={"supplier_invoice_number": 0.98},
            created_by=self.hr_user,
            confirmed_by=self.hr_user if status == ExtractionStatus.CONFIRMED else None,
        )

    def _create_payload(self, job):
        return {"extraction_job": job.id, "supplier": self.supplier.id, "overrides": {}}

    def _create_invoice(self, total_amount=Decimal("450.00")):
        return SupplierInvoice.objects.create(
            invoice_record_number=f"SIR-LY-2026-{SupplierInvoice.objects.count() + 1:06d}",
            supplier=self.supplier,
            supplier_invoice_number=f"INV-AFR-{SupplierInvoice.objects.count() + 8000}",
            invoice_date=date(2026, 7, 5),
            received_date=date(2026, 7, 6),
            total_amount=total_amount,
            created_by=self.hr_user,
        )

    def _create_line(self, invoice, ticket_number="1761234567890", invoiced_amount=Decimal("450.00")):
        return SupplierInvoiceLine.objects.create(
            supplier_invoice=invoice,
            ticket_number=ticket_number,
            route_from="CAI",
            route_to="TIP",
            invoiced_amount=invoiced_amount,
            account_type=AccountType.COMPANY,
        )
