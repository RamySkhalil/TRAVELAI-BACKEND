from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.billing_confirmations.models import BillingConfirmationStatus, FinanceStatus, TravelBillingConfirmationNote
from apps.dashboard.services import TICKET_INVOICE_FOLLOW_UP_DAYS
from apps.master_data.models import Country, Department, Employee, Project, Supplier
from apps.permits.models import Permit, PermitStatus, PermitType
from apps.supplier_invoices.models import MatchStatus, SupplierInvoice, SupplierInvoiceLine, SupplierInvoiceStatus
from apps.ticket_versions.models import TicketAction, TicketBillingState, TicketVersion
from apps.travel_cases.models import AccountType, TravelCase, TravelCaseStatus, TravelPurpose


class DashboardPhase13Tests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user_model = get_user_model()
        self.admin_user = self._create_user("admin-user", "Admin")
        self.hr_user = self._create_user("hr-user", "HR")
        self.booking_user = self._create_user("booking-user", "BookingOfficer")
        self.finance_user = self._create_user("finance-user", "Finance")
        self.client.force_authenticate(self.admin_user)

        self.country = Country.objects.create(code="LY", name="Libya")
        self.department = Department.objects.create(code="OPS", name="Operations")
        self.project = Project.objects.create(code="TRIP", name="Tripoli Ops", country=self.country)
        self.second_project = Project.objects.create(code="SIRTE", name="Sirte Field Ops", country=self.country)
        self.supplier = Supplier.objects.create(code="AFR", name="Afriqiyah Airways")
        self.employee = Employee.objects.create(
            badge_number="E001",
            full_name="Aisha Mohamed",
            project=self.project,
            department=self.department,
        )
        self.travel_case = self._create_travel_case("TRV-LY-2026-000001", self.project, TravelCaseStatus.SUBMITTED_BY_HR)
        self.second_case = self._create_travel_case("TRV-LY-2026-000002", self.second_project, TravelCaseStatus.UNDER_BOOKING)
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

    def test_dashboard_summary_returns_core_keys(self):
        self._create_invoice(1, SupplierInvoiceStatus.AWAITING_MATCHING, Decimal("100.00"), days_old=1)

        response = self.client.get("/api/v1/dashboard/summary/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("travel", response.data)
        self.assertIn("tickets", response.data)
        self.assertIn("permits", response.data)
        self.assertIn("supplier_invoices", response.data)
        self.assertIn("tbcn_finance", response.data)
        self.assertEqual(response.data["travel"]["submitted"], 1)

    def test_cost_by_month_groups_invoice_totals(self):
        self._create_invoice(1, SupplierInvoiceStatus.MATCHED, Decimal("100.00"), days_old=1, invoice_date=date(2026, 6, 5))
        self._create_invoice(2, SupplierInvoiceStatus.MATCHED, Decimal("50.00"), days_old=1, invoice_date=date(2026, 6, 20))

        response = self.client.get("/api/v1/dashboard/cost-by-month/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["results"][0]["month"], "2026-06")
        self.assertEqual(response.data["results"][0]["currency"], "USD")
        self.assertEqual(response.data["results"][0]["invoice_count"], 2)
        self.assertEqual(response.data["results"][0]["total_amount"], Decimal("150.00"))

    def test_cost_by_month_groups_totals_by_currency(self):
        self._create_invoice(1, SupplierInvoiceStatus.MATCHED, Decimal("100.00"), days_old=1, invoice_date=date(2026, 6, 5), currency="USD")
        self._create_invoice(2, SupplierInvoiceStatus.MATCHED, Decimal("18500.00"), days_old=1, invoice_date=date(2026, 6, 20), currency="EGP")

        response = self.client.get("/api/v1/dashboard/cost-by-month/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        rows = {(row["month"], row["currency"]): row["total_amount"] for row in response.data["results"]}
        self.assertEqual(rows[("2026-06", "USD")], Decimal("100.00"))
        self.assertEqual(rows[("2026-06", "EGP")], Decimal("18500.00"))

    def test_dashboard_summary_groups_unpaid_totals_by_currency(self):
        usd_invoice = self._create_invoice(1, SupplierInvoiceStatus.TBCN_GENERATED, Decimal("100.00"), days_old=1, currency="USD")
        egp_invoice = self._create_invoice(2, SupplierInvoiceStatus.TBCN_GENERATED, Decimal("18500.00"), days_old=1, currency="EGP")
        self._create_tbcn(usd_invoice, "TBCN-LY-2026-000101", BillingConfirmationStatus.GENERATED, FinanceStatus.NOT_SENT)
        self._create_tbcn(egp_invoice, "TBCN-EG-2026-000102", BillingConfirmationStatus.GENERATED, FinanceStatus.NOT_SENT)

        response = self.client.get("/api/v1/dashboard/summary/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        amounts = {row["currency"]: row["amount"] for row in response.data["tbcn_finance"]["unpaid_by_currency"]}
        self.assertEqual(amounts["USD"], Decimal("100.00"))
        self.assertEqual(amounts["EGP"], Decimal("18500.00"))
        self.assertNotIn("unpaid_amount", response.data["tbcn_finance"])

    def test_cost_by_project_uses_linked_travel_cases(self):
        invoice = self._create_invoice(1, SupplierInvoiceStatus.MATCHED, Decimal("150.00"), days_old=1)
        self._create_line(invoice, self.travel_case, Decimal("125.00"))

        response = self.client.get("/api/v1/dashboard/cost-by-project/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["results"][0]["project_code"], self.project.code)
        self.assertEqual(response.data["results"][0]["total_amount"], Decimal("125.00"))

    def test_cost_by_supplier_groups_invoice_totals(self):
        self._create_invoice(1, SupplierInvoiceStatus.MATCHED, Decimal("100.00"), days_old=1)
        self._create_invoice(2, SupplierInvoiceStatus.MATCHED, Decimal("25.00"), days_old=2)

        response = self.client.get("/api/v1/dashboard/cost-by-supplier/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["results"][0]["supplier_name"], self.supplier.name)
        self.assertEqual(response.data["results"][0]["invoice_count"], 2)
        self.assertEqual(response.data["results"][0]["total_amount"], Decimal("125.00"))

    def test_supplier_aging_buckets_pending_invoice_and_finance_records(self):
        self._create_invoice(1, SupplierInvoiceStatus.MATCHED, Decimal("100.00"), days_old=3)
        self._create_invoice(2, SupplierInvoiceStatus.HR_APPROVED, Decimal("200.00"), days_old=10)
        tbcn_invoice = self._create_invoice(3, SupplierInvoiceStatus.TBCN_GENERATED, Decimal("300.00"), days_old=20)
        self._create_tbcn(tbcn_invoice, "TBCN-LY-2026-000001", BillingConfirmationStatus.GENERATED, FinanceStatus.NOT_SENT)
        finance_invoice = self._create_invoice(4, SupplierInvoiceStatus.SENT_TO_FINANCE, Decimal("400.00"), days_old=40)
        self._create_tbcn(finance_invoice, "TBCN-LY-2026-000002", BillingConfirmationStatus.SENT_TO_FINANCE, FinanceStatus.SENT)

        response = self.client.get("/api/v1/dashboard/supplier-aging/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        pairs = {(row["stage"], row["bucket"]) for row in response.data["results"]}
        self.assertIn(("RECEIVED_NOT_HR_APPROVED", "0_7"), pairs)
        self.assertIn(("HR_APPROVED_NO_TBCN", "8_14"), pairs)
        self.assertIn(("TBCN_GENERATED_NOT_SENT", "15_30"), pairs)
        self.assertIn(("SENT_TO_FINANCE_NOT_PAID", "over_30"), pairs)

    def test_pending_actions_includes_submitted_travel_cases_for_booking_officer(self):
        self.client.force_authenticate(self.booking_user)

        response = self.client.get("/api/v1/dashboard/pending-actions/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(any(item["type"] == "TRAVEL_CASE" and item["status"] == TravelCaseStatus.SUBMITTED_BY_HR for item in response.data["items"]))

    def test_pending_actions_includes_hr_approved_invoices_ready_for_tbcn_for_hr(self):
        self._create_invoice(1, SupplierInvoiceStatus.HR_APPROVED, Decimal("100.00"), days_old=1)
        self.client.force_authenticate(self.hr_user)

        response = self.client.get("/api/v1/dashboard/pending-actions/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(any(item["type"] == "TBCN_READY" for item in response.data["items"]))

    def test_pending_actions_includes_sent_to_finance_tbcns_for_finance(self):
        invoice = self._create_invoice(1, SupplierInvoiceStatus.SENT_TO_FINANCE, Decimal("100.00"), days_old=1)
        self._create_tbcn(invoice, "TBCN-LY-2026-000001", BillingConfirmationStatus.SENT_TO_FINANCE, FinanceStatus.SENT)
        self.client.force_authenticate(self.finance_user)

        response = self.client.get("/api/v1/dashboard/pending-actions/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(any(item["type"] == "TBCN" and item["status"] == FinanceStatus.SENT for item in response.data["items"]))

    def test_pending_permits_appear(self):
        Permit.objects.create(travel_case=self.travel_case, permit_type=PermitType.LIBYA_PERMIT, status=PermitStatus.PENDING)
        self.client.force_authenticate(self.hr_user)

        response = self.client.get("/api/v1/dashboard/pending-actions/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(any(item["type"] == "PERMIT" for item in response.data["items"]))

    def test_summary_counts_tickets_awaiting_a_supplier_invoice(self):
        self._mark_awaiting_invoice(self.ticket_version, days_ago=1)

        response = self.client.get("/api/v1/dashboard/summary/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["tickets"]["awaiting_invoice"], 1)
        self.assertEqual(response.data["tickets"]["awaiting_invoice_overdue"], 0)
        amounts = {row["currency"]: row["amount"] for row in response.data["tickets"]["unbilled_by_currency"]}
        self.assertEqual(amounts["USD"], Decimal("450.00"))

    def test_unbilled_tickets_report_groups_by_supplier(self):
        self._mark_awaiting_invoice(self.ticket_version, days_ago=TICKET_INVOICE_FOLLOW_UP_DAYS + 5)

        response = self.client.get("/api/v1/dashboard/unbilled-tickets/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        row = response.data["results"][0]
        self.assertEqual(row["supplier_code"], self.supplier.code)
        self.assertEqual(row["ticket_count"], 1)
        self.assertEqual(row["total_amount"], Decimal("450.00"))
        self.assertEqual(row["overdue_count"], 1)
        self.assertGreaterEqual(row["oldest_age_days"], TICKET_INVOICE_FOLLOW_UP_DAYS)

    def test_overdue_unbilled_ticket_appears_in_the_booking_queue(self):
        self._mark_awaiting_invoice(self.ticket_version, days_ago=TICKET_INVOICE_FOLLOW_UP_DAYS + 1)
        self.client.force_authenticate(self.booking_user)

        response = self.client.get("/api/v1/dashboard/pending-actions/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(any(item["type"] == "TICKET_AWAITING_INVOICE" for item in response.data["items"]))

    def test_recently_booked_ticket_stays_out_of_the_queue(self):
        self._mark_awaiting_invoice(self.ticket_version, days_ago=1)
        self.client.force_authenticate(self.booking_user)

        response = self.client.get("/api/v1/dashboard/pending-actions/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(any(item["type"] == "TICKET_AWAITING_INVOICE" for item in response.data["items"]))

    def test_dashboard_endpoints_require_authentication(self):
        self.client.force_authenticate(user=None)

        for endpoint in [
            "/api/v1/dashboard/summary/",
            "/api/v1/dashboard/operational-kpis/",
            "/api/v1/dashboard/cost-by-month/",
            "/api/v1/dashboard/cost-by-project/",
            "/api/v1/dashboard/cost-by-supplier/",
            "/api/v1/dashboard/cost-by-route/",
            "/api/v1/dashboard/supplier-aging/",
            "/api/v1/dashboard/unbilled-tickets/",
            "/api/v1/dashboard/pending-actions/",
        ]:
            with self.subTest(endpoint=endpoint):
                response = self.client.get(endpoint)
                self.assertIn(response.status_code, [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN])

    def _create_user(self, username, group_name):
        user = self.user_model.objects.create_user(username=username, password="test-pass")
        group, _ = Group.objects.get_or_create(name=group_name)
        user.groups.add(group)
        return user

    def _mark_awaiting_invoice(self, ticket, days_ago):
        ticket.billing_state = TicketBillingState.AWAITING_INVOICE
        ticket.billing_state_changed_at = timezone.now() - timedelta(days=days_ago)
        ticket.save(update_fields=["billing_state", "billing_state_changed_at"])
        return ticket

    def _create_travel_case(self, case_number, project, status_value):
        return TravelCase.objects.create(
            case_number=case_number,
            employee=self.employee,
            badge_number=self.employee.badge_number,
            employee_name=self.employee.full_name,
            project=project,
            department=self.department,
            country=self.country,
            travel_purpose=TravelPurpose.BUSINESS_TRIP,
            account_type=AccountType.COMPANY,
            route_from="CAI",
            route_to="TIP",
            requested_travel_date=date(2026, 7, 1),
            current_status=status_value,
            assigned_to=self.booking_user,
            submitted_at=timezone.now(),
            created_by=self.hr_user,
        )

    def _create_invoice(self, index, status_value, total_amount, days_old, invoice_date=None, currency="USD"):
        received_date = timezone.localdate() - timedelta(days=days_old)
        return SupplierInvoice.objects.create(
            invoice_record_number=f"INV-LY-2026-{index:06d}",
            supplier=self.supplier,
            supplier_invoice_number=f"SUP-{index:04d}",
            invoice_date=invoice_date or received_date,
            received_date=received_date,
            currency=currency,
            total_amount=total_amount,
            status=status_value,
            created_by=self.hr_user,
            approved_by=self.hr_user if status_value == SupplierInvoiceStatus.HR_APPROVED else None,
            approved_at=timezone.now() if status_value == SupplierInvoiceStatus.HR_APPROVED else None,
        )

    def _create_line(self, invoice, travel_case, invoiced_amount):
        return SupplierInvoiceLine.objects.create(
            supplier_invoice=invoice,
            travel_case=travel_case,
            ticket_version=self.ticket_version,
            employee=self.employee,
            ticket_number=self.ticket_version.ticket_number,
            route_from=self.ticket_version.route_from,
            route_to=self.ticket_version.route_to,
            booked_amount=self.ticket_version.amount,
            invoiced_amount=invoiced_amount,
            currency=invoice.currency,
            account_type=AccountType.COMPANY,
            match_status=MatchStatus.MATCHED,
        )

    def _create_tbcn(self, invoice, confirmation_no, status_value, finance_status):
        return TravelBillingConfirmationNote.objects.create(
            confirmation_no=confirmation_no,
            supplier_invoice=invoice,
            supplier=self.supplier,
            supplier_invoice_number=invoice.supplier_invoice_number,
            total_amount=invoice.total_amount,
            matched_amount=invoice.total_amount,
            difference_amount=Decimal("0.00"),
            currency=invoice.currency,
            status=status_value,
            finance_status=finance_status,
            generated_by=self.hr_user,
            sent_to_finance_by=self.hr_user if finance_status in [FinanceStatus.SENT, FinanceStatus.ACCEPTED, FinanceStatus.PAID] else None,
            sent_to_finance_at=timezone.now() if finance_status in [FinanceStatus.SENT, FinanceStatus.ACCEPTED, FinanceStatus.PAID] else None,
        )
