from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.invoice_matching.services import match_invoice, resolve_invoice_line_exception
from apps.master_data.models import Country, Department, Employee, Project, Supplier
from apps.supplier_invoices.models import MatchStatus, SupplierInvoice, SupplierInvoiceLine, SupplierInvoiceStatus
from apps.supplier_invoices.services import approve_supplier_invoice
from apps.ticket_versions.models import TicketAction, TicketBillingState, TicketVersion
from apps.travel_cases.models import AccountType, TravelCase, TravelCaseStatus, TravelPurpose


class InvoiceMatchingCasePropagationTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="match-user", password="test-pass")
        self.country = Country.objects.create(code="LY", name="Libya")
        self.department = Department.objects.create(code="OPS", name="Operations")
        self.project = Project.objects.create(code="TRIP", name="Tripoli Ops", country=self.country)
        self.supplier = Supplier.objects.create(code="AFR", name="Afriqiyah Airways")
        self.employee = Employee.objects.create(
            badge_number="E001", full_name="Aisha Mohamed", project=self.project, department=self.department
        )
        self.travel_case = TravelCase.objects.create(
            case_number="TRV-LY-2026-000001",
            employee=self.employee,
            badge_number="E001",
            employee_name="Aisha Mohamed",
            project=self.project,
            department=self.department,
            country=self.country,
            travel_purpose=TravelPurpose.BUSINESS_TRIP,
            account_type=AccountType.COMPANY,
            route_from="CAI",
            route_to="TIP",
            requested_travel_date=date(2026, 7, 1),
            current_status=TravelCaseStatus.TICKET_BOOKED,
            created_by=self.user,
        )
        self.ticket = TicketVersion.objects.create(
            travel_case=self.travel_case,
            version_number="V1",
            ticket_action=TicketAction.ORIGINAL,
            passenger_name="Aisha Mohamed",
            ticket_number="TICKET-1",
            pnr="ABC123",
            airline="Afriqiyah Airways",
            route_from="CAI",
            route_to="TIP",
            departure_date=date(2026, 7, 1),
            amount=Decimal("450.00"),
            currency="USD",
            supplier=self.supplier,
        )
        self.invoice = SupplierInvoice.objects.create(
            invoice_record_number="SIR-LY-2026-000001",
            supplier=self.supplier,
            supplier_invoice_number="INV-1",
            invoice_date=date(2026, 7, 5),
            received_date=date(2026, 7, 6),
            currency="USD",
            total_amount=Decimal("450.00"),
            status=SupplierInvoiceStatus.AWAITING_MATCHING,
            created_by=self.user,
        )
        self.line = SupplierInvoiceLine.objects.create(
            supplier_invoice=self.invoice,
            ticket_number="TICKET-1",
            route_from="CAI",
            route_to="TIP",
            invoiced_amount=Decimal("450.00"),
            currency="USD",
            account_type=AccountType.COMPANY,
            match_status=MatchStatus.UNMATCHED,
        )

    def test_match_advances_linked_case_to_invoice_matched(self):
        match_invoice(self.invoice, self.user)

        self.travel_case.refresh_from_db()
        self.assertEqual(self.travel_case.current_status, TravelCaseStatus.INVOICE_MATCHED)

    def test_ticket_not_found_does_not_advance_case(self):
        self.line.ticket_number = "MISSING"
        self.line.save(update_fields=["ticket_number"])

        match_invoice(self.invoice, self.user)

        self.travel_case.refresh_from_db()
        self.assertEqual(self.travel_case.current_status, TravelCaseStatus.TICKET_BOOKED)

    def test_match_closes_the_ticket_payable(self):
        self.ticket.billing_state = TicketBillingState.AWAITING_INVOICE
        self.ticket.save(update_fields=["billing_state"])

        match_invoice(self.invoice, self.user)

        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.billing_state, TicketBillingState.INVOICED)

    def test_rerunning_matching_preserves_a_resolved_exception(self):
        self.line.ticket_number = "MISSING"
        self.line.save(update_fields=["ticket_number"])
        match_invoice(self.invoice, self.user)
        self.line.refresh_from_db()
        self.assertEqual(self.line.match_status, MatchStatus.EXCEPTION)
        resolve_invoice_line_exception(self.line, "Agency confirmed the ticket by email", self.user)

        match_invoice(self.invoice, self.user)

        self.line.refresh_from_db()
        self.assertEqual(self.line.match_status, MatchStatus.APPROVED)
        self.assertEqual(self.line.exception_reason, "Agency confirmed the ticket by email")

    def test_rerunning_matching_after_resolution_keeps_invoice_approvable(self):
        self.line.ticket_number = "MISSING"
        self.line.save(update_fields=["ticket_number"])
        match_invoice(self.invoice, self.user)
        self.line.refresh_from_db()
        resolve_invoice_line_exception(self.line, "Agency confirmed the ticket by email", self.user)

        match_invoice(self.invoice, self.user)
        self.invoice.refresh_from_db()
        approve_supplier_invoice(self.invoice, self.user)

        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, SupplierInvoiceStatus.HR_APPROVED)

    def test_resolving_last_exception_clears_the_invoice_exception_status(self):
        self.line.ticket_number = "MISSING"
        self.line.save(update_fields=["ticket_number"])
        match_invoice(self.invoice, self.user)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, SupplierInvoiceStatus.EXCEPTION_FOUND)
        self.line.refresh_from_db()

        resolve_invoice_line_exception(self.line, "Ticket number corrected by the agency", self.user)

        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, SupplierInvoiceStatus.MATCHED)

    def test_remaining_exception_keeps_the_invoice_in_exception(self):
        self.line.ticket_number = "MISSING"
        self.line.save(update_fields=["ticket_number"])
        second_line = SupplierInvoiceLine.objects.create(
            supplier_invoice=self.invoice,
            ticket_number="ALSO-MISSING",
            invoiced_amount=Decimal("120.00"),
            currency="USD",
            account_type=AccountType.COMPANY,
        )
        match_invoice(self.invoice, self.user)
        self.line.refresh_from_db()

        resolve_invoice_line_exception(self.line, "Confirmed by the agency", self.user)

        self.invoice.refresh_from_db()
        second_line.refresh_from_db()
        self.assertEqual(second_line.match_status, MatchStatus.EXCEPTION)
        self.assertEqual(self.invoice.status, SupplierInvoiceStatus.EXCEPTION_FOUND)

    def test_resolving_a_currency_exception_advances_the_case_and_ticket(self):
        self.ticket.billing_state = TicketBillingState.AWAITING_INVOICE
        self.ticket.save(update_fields=["billing_state"])
        self.line.currency = "EGP"
        self.line.save(update_fields=["currency"])
        match_invoice(self.invoice, self.user)
        self.travel_case.refresh_from_db()
        self.assertEqual(self.travel_case.current_status, TravelCaseStatus.TICKET_BOOKED)
        self.line.refresh_from_db()

        resolve_invoice_line_exception(self.line, "Agency billed in EGP by agreement", self.user)

        self.travel_case.refresh_from_db()
        self.ticket.refresh_from_db()
        self.assertEqual(self.travel_case.current_status, TravelCaseStatus.INVOICE_MATCHED)
        self.assertEqual(self.ticket.billing_state, TicketBillingState.INVOICED)

    def test_resolution_does_not_move_an_already_approved_invoice(self):
        self.line.currency = "EGP"
        self.line.save(update_fields=["currency"])
        match_invoice(self.invoice, self.user)
        self.line.refresh_from_db()
        self.invoice.status = SupplierInvoiceStatus.HR_APPROVED
        self.invoice.save(update_fields=["status"])

        resolve_invoice_line_exception(self.line, "Agency billed in EGP by agreement", self.user)

        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, SupplierInvoiceStatus.HR_APPROVED)
