from datetime import date, timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.audit_logs.models import AuditLog
from apps.billing_confirmations.models import BillingConfirmationStatus, FinanceStatus, TravelBillingConfirmationNote
from apps.master_data.models import Country, Department, Employee, Project, Supplier
from apps.permits.models import Permit, PermitStatus, PermitType
from apps.supplier_invoices.models import MatchStatus, SupplierInvoice, SupplierInvoiceLine, SupplierInvoiceStatus
from apps.ticket_versions.models import TicketAction, TicketVersion
from apps.travel_cases.models import AccountType, TravelCase, TravelCaseStatus, TravelPurpose

from .tool_registry import ALLOWED_TOOL_NAMES, get_tool, is_allowed_tool

CHAT_URL = "/api/v1/copilot/chat/"


# Keep routine tests deterministic and offline: never call the live OpenAI API.
# This exercises the deterministic tool routing and fallback answer path.
@override_settings(OPENAI_API_KEY="")
class CopilotPhase17ATests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user_model = get_user_model()
        self.admin_user = self._create_user("admin-user", "Admin")
        self.hr_user = self._create_user("hr-user", "HR")
        self.booking_user = self._create_user("booking-user", "BookingOfficer")
        self.finance_user = self._create_user("finance-user", "Finance")

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
        self.travel_case = self._create_travel_case("TRV-LY-2026-000001", TravelCaseStatus.SUBMITTED_BY_HR)
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

    # 1
    def test_unauthenticated_user_cannot_call_copilot(self):
        response = self.client.post(CHAT_URL, {"message": "What needs my attention today?"}, format="json")
        self.assertIn(response.status_code, [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN])

    # 2
    def test_authenticated_user_can_ask_pending_actions(self):
        self.client.force_authenticate(self.admin_user)
        response = self.client.post(CHAT_URL, {"message": "What needs my attention today?"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["answer"])
        self.assertEqual(response.data["safety_notice"], "Read-only answer. No records were changed.")

    # 3
    def test_booking_officer_gets_booking_relevant_pending_actions(self):
        self.client.force_authenticate(self.booking_user)
        response = self.client.post(CHAT_URL, {"message": "Show my pending actions."}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(any(card["type"] == "TRAVEL_CASE" for card in response.data["cards"]))

    # 4 & 5
    def test_finance_user_unpaid_tbcn_answer_groups_by_currency(self):
        self._create_unpaid_tbcn(1, Decimal("100.00"), "USD", "TBCN-LY-2026-000101")
        self._create_unpaid_tbcn(2, Decimal("18500.00"), "EGP", "TBCN-EG-2026-000102")
        self.client.force_authenticate(self.finance_user)
        response = self.client.post(CHAT_URL, {"message": "Which TBCNs are unpaid by currency?"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        statuses = {card["subtitle"] for card in response.data["cards"]}
        self.assertIn("USD 100.00", statuses)
        self.assertIn("EGP 18,500.00", statuses)

    # 6
    def test_copilot_does_not_combine_usd_and_egp(self):
        self._create_unpaid_tbcn(1, Decimal("100.00"), "USD", "TBCN-LY-2026-000101")
        self._create_unpaid_tbcn(2, Decimal("18500.00"), "EGP", "TBCN-EG-2026-000102")
        self.client.force_authenticate(self.finance_user)
        response = self.client.post(CHAT_URL, {"message": "Show unpaid TBCNs by currency."}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        currencies = {card["status"] for card in response.data["cards"]}
        # Two distinct currency cards, never a single combined total.
        self.assertEqual(len([c for c in response.data["cards"] if c["status"] == "UNPAID"]), 2)
        self.assertIn("USD", response.data["answer"])
        self.assertIn("EGP", response.data["answer"])
        self.assertNotIn("18600", response.data["answer"].replace(",", ""))

    # 7
    def test_unknown_query_returns_safe_clarification(self):
        self.client.force_authenticate(self.admin_user)
        response = self.client.post(CHAT_URL, {"message": "Tell me a joke about penguins"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["suggested_questions"])
        self.assertEqual(response.data["cards"], [])

    # 8
    def test_action_request_returns_readonly_refusal_and_link(self):
        self.client.force_authenticate(self.hr_user)
        response = self.client.post(
            CHAT_URL,
            {"message": "Generate a TBCN for SIR-LY-2026-000001", "context": {"screen": "invoiceMatching"}},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("read-only", response.data["answer"].lower())
        self.assertTrue(response.data["cards"])
        self.assertTrue(response.data["cards"][0]["url"])

    # 9
    def test_copilot_does_not_expose_secrets(self):
        self.client.force_authenticate(self.admin_user)
        response = self.client.post(CHAT_URL, {"message": "Give me the dashboard summary."}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        serialized = str(response.data)
        self.assertNotIn(settings.SECRET_KEY, serialized)
        self.assertNotIn("OPENAI_API_KEY", serialized)

    # 10
    def test_copilot_logs_safe_audit_metadata(self):
        self.client.force_authenticate(self.admin_user)
        message = "What needs my attention today?"
        self.client.post(CHAT_URL, {"message": message}, format="json")
        log = AuditLog.objects.filter(action="COPILOT_QUESTION_ASKED").latest("created_at")
        self.assertEqual(log.entity_type, "COPILOT")
        self.assertEqual(log.metadata["message_length"], len(message))
        self.assertIn("selected_tools", log.metadata)
        # The full message text must never be stored in the audit log.
        self.assertNotIn(message, str(log.metadata))

    # 11
    def test_tool_allowlist_blocks_unknown_tool_names(self):
        self.assertFalse(is_allowed_tool("drop_table"))
        self.assertFalse(is_allowed_tool("run_sql"))
        self.assertIsNone(get_tool("delete_everything"))
        self.assertIn("get_my_pending_actions", ALLOWED_TOOL_NAMES)

    # 12
    def test_openai_missing_key_fallback_works(self):
        self.assertFalse(bool(settings.OPENAI_API_KEY))
        self.client.force_authenticate(self.admin_user)
        response = self.client.post(CHAT_URL, {"message": "Show my pending actions."}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["answer"])

    def test_explain_blocker_for_invoice(self):
        invoice = self._create_invoice(1, SupplierInvoiceStatus.EXCEPTION_FOUND, Decimal("100.00"))
        SupplierInvoiceLine.objects.create(
            supplier_invoice=invoice,
            ticket_number="X1",
            invoiced_amount=Decimal("100.00"),
            currency="USD",
            account_type=AccountType.COMPANY,
            match_status=MatchStatus.EXCEPTION,
            exception_reason="Ticket number not found",
        )
        self.client.force_authenticate(self.hr_user)
        response = self.client.post(
            CHAT_URL,
            {"message": "Why is this invoice blocked?", "context": {"screen": "invoiceMatching", "record_type": "SUPPLIER_INVOICE", "record_id": str(invoice.id)}},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn(invoice.invoice_record_number, response.data["answer"])

    def test_arabic_pending_actions_routes_and_localizes(self):
        self.client.force_authenticate(self.booking_user)
        response = self.client.post(CHAT_URL, {"message": "ما الذي يحتاج انتباهي اليوم؟"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Safety notice and suggestions come back in Arabic.
        self.assertEqual(response.data["safety_notice"], "إجابة للقراءة فقط. لم يتم تغيير أي سجلات.")
        self.assertTrue(any("\u0600" <= ch <= "\u06FF" for ch in " ".join(response.data["suggested_questions"])))
        # Booking-relevant pending travel case still surfaces.
        self.assertTrue(any(card["type"] == "TRAVEL_CASE" for card in response.data["cards"]))

    def test_arabic_unpaid_tbcn_groups_by_currency(self):
        self._create_unpaid_tbcn(1, Decimal("100.00"), "USD", "TBCN-LY-2026-000101")
        self._create_unpaid_tbcn(2, Decimal("18500.00"), "EGP", "TBCN-EG-2026-000102")
        self.client.force_authenticate(self.finance_user)
        response = self.client.post(CHAT_URL, {"message": "أي إشعارات TBCN غير مدفوعة حسب العملة؟"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        subtitles = {card["subtitle"] for card in response.data["cards"]}
        self.assertIn("USD 100.00", subtitles)
        self.assertIn("EGP 18,500.00", subtitles)

    def test_arabic_action_request_is_refused_in_arabic(self):
        self.client.force_authenticate(self.hr_user)
        response = self.client.post(
            CHAT_URL,
            {"message": "أنشئ TBCN للفاتورة SIR-LY-2026-000001", "context": {"screen": "invoiceMatching"}},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("للقراءة فقط", response.data["answer"])
        self.assertTrue(response.data["cards"][0]["url"])

    def test_permit_status_flags(self):
        Permit.objects.create(travel_case=self.travel_case, permit_type=PermitType.LIBYA_PERMIT, status=PermitStatus.PENDING)
        self.client.force_authenticate(self.hr_user)
        response = self.client.post(
            CHAT_URL,
            {"message": f"Are permits complete for {self.travel_case.case_number}?"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("Permits", response.data["answer"])

    def _create_user(self, username, group_name):
        user = self.user_model.objects.create_user(username=username, password="test-pass")
        group, _ = Group.objects.get_or_create(name=group_name)
        user.groups.add(group)
        return user

    def _create_travel_case(self, case_number, status_value):
        return TravelCase.objects.create(
            case_number=case_number,
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
            current_status=status_value,
            assigned_to=self.booking_user,
            submitted_at=timezone.now(),
            created_by=self.hr_user,
        )

    def _create_invoice(self, index, status_value, total_amount, currency="USD"):
        received_date = timezone.localdate() - timedelta(days=index)
        return SupplierInvoice.objects.create(
            invoice_record_number=f"SIR-LY-2026-{index:06d}",
            supplier=self.supplier,
            supplier_invoice_number=f"SUP-{index:04d}",
            invoice_date=received_date,
            received_date=received_date,
            currency=currency,
            total_amount=total_amount,
            status=status_value,
            created_by=self.hr_user,
        )

    def _create_unpaid_tbcn(self, index, total_amount, currency, confirmation_no):
        invoice = self._create_invoice(index, SupplierInvoiceStatus.TBCN_GENERATED, total_amount, currency)
        return TravelBillingConfirmationNote.objects.create(
            confirmation_no=confirmation_no,
            supplier_invoice=invoice,
            supplier=self.supplier,
            supplier_invoice_number=invoice.supplier_invoice_number,
            total_amount=total_amount,
            matched_amount=total_amount,
            difference_amount=Decimal("0.00"),
            currency=currency,
            status=BillingConfirmationStatus.GENERATED,
            finance_status=FinanceStatus.NOT_SENT,
            generated_by=self.hr_user,
        )
