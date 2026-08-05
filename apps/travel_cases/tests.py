from datetime import date

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from apps.audit_logs.models import AuditLog
from apps.master_data.models import Country, Department, Employee, Project
from apps.travel_cases.models import AccountType, TravelCase, TravelCaseStatus, TravelPurpose
from apps.travel_cases.services import (
    advance_travel_case_status,
    cancel_travel_case,
    close_travel_case,
    postpone_travel_case,
    request_travel_case_change,
)


class TravelCaseLifecycleTests(TestCase):
    def setUp(self):
        self.user_model = get_user_model()
        self.user = self._create_user("hr-user", "HR")
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.country = Country.objects.create(code="LY", name="Libya")
        self.department = Department.objects.create(code="OPS", name="Operations")
        self.project = Project.objects.create(code="TRIP", name="Tripoli Ops", country=self.country)
        self.employee = Employee.objects.create(
            badge_number="E001",
            full_name="Aisha Mohamed",
            project=self.project,
            department=self.department,
        )

    def test_advance_is_forward_only_and_idempotent(self):
        case = self._create_case(TravelCaseStatus.TICKET_BOOKED)

        advance_travel_case_status(case, TravelCaseStatus.INVOICE_MATCHED, self.user, action="Travel Case Invoice Matched")
        advance_travel_case_status(case, TravelCaseStatus.TICKET_BOOKED, self.user, action="Travel Case Invoice Matched")
        advance_travel_case_status(case, TravelCaseStatus.INVOICE_MATCHED, self.user, action="Travel Case Invoice Matched")

        case.refresh_from_db()
        self.assertEqual(case.current_status, TravelCaseStatus.INVOICE_MATCHED)
        self.assertEqual(AuditLog.objects.filter(action="Travel Case Invoice Matched", entity_id=case.id).count(), 1)

    def test_advance_never_touches_terminal_cases(self):
        case = self._create_case(TravelCaseStatus.CANCELLED)

        advance_travel_case_status(case, TravelCaseStatus.PAID, self.user)

        case.refresh_from_db()
        self.assertEqual(case.current_status, TravelCaseStatus.CANCELLED)

    def test_cancel_requires_reason_and_sets_cancelled(self):
        case = self._create_case(TravelCaseStatus.TICKET_BOOKED)

        with self.assertRaises(ValidationError):
            cancel_travel_case(case, "", self.user)

        cancel_travel_case(case, "Traveller resigned", self.user)
        case.refresh_from_db()
        self.assertEqual(case.current_status, TravelCaseStatus.CANCELLED)
        self.assertTrue(AuditLog.objects.filter(action="Travel Case Cancelled", entity_id=case.id).exists())

    def test_paid_case_cannot_be_cancelled(self):
        case = self._create_case(TravelCaseStatus.PAID)

        with self.assertRaises(ValidationError):
            cancel_travel_case(case, "Too late", self.user)

        case.refresh_from_db()
        self.assertEqual(case.current_status, TravelCaseStatus.PAID)

    def test_postpone_then_request_change(self):
        case = self._create_case(TravelCaseStatus.TICKET_BOOKED)

        postpone_travel_case(case, "Visa delay", self.user)
        case.refresh_from_db()
        self.assertEqual(case.current_status, TravelCaseStatus.POSTPONED)

        request_travel_case_change(case, "New dates", self.user)
        case.refresh_from_db()
        self.assertEqual(case.current_status, TravelCaseStatus.CHANGE_REQUESTED)

    def test_case_can_be_closed_after_paid(self):
        case = self._create_case(TravelCaseStatus.PAID)

        close_travel_case(case, self.user)

        case.refresh_from_db()
        self.assertEqual(case.current_status, TravelCaseStatus.CLOSED)

    def test_close_blocked_before_final_status(self):
        case = self._create_case(TravelCaseStatus.TICKET_BOOKED)

        with self.assertRaises(ValidationError):
            close_travel_case(case, self.user)

    def test_cancel_via_api(self):
        case = self._create_case(TravelCaseStatus.TICKET_BOOKED)

        response = self.client.post(
            f"/api/v1/travel-cases/{case.id}/cancel/",
            {"reason": "Traveller resigned"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["current_status"], TravelCaseStatus.CANCELLED)

    def test_cancel_via_api_requires_reason(self):
        case = self._create_case(TravelCaseStatus.TICKET_BOOKED)

        response = self.client.post(f"/api/v1/travel-cases/{case.id}/cancel/", {"reason": ""}, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        case.refresh_from_db()
        self.assertEqual(case.current_status, TravelCaseStatus.TICKET_BOOKED)

    def test_detail_exposes_created_and_assigned_usernames(self):
        booking_user = self._create_user("booking-user", "BookingOfficer")
        case = self._create_case()
        case.assigned_to = booking_user
        case.save(update_fields=["assigned_to"])

        response = self.client.get(f"/api/v1/travel-cases/{case.id}/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["created_by_username"], self.user.username)
        self.assertEqual(response.data["assigned_to_username"], booking_user.username)

    def test_timeline_exposes_actor_username(self):
        case = self._create_case(TravelCaseStatus.TICKET_BOOKED)
        cancel_travel_case(case, "Traveller resigned", self.user)

        response = self.client.get(f"/api/v1/travel-cases/{case.id}/timeline/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        events = response.data["events"]
        self.assertTrue(events)
        self.assertEqual(events[-1]["user_username"], self.user.username)

    def test_travel_cases_can_be_searched_by_case_number(self):
        match = self._create_case(number="TRV-LY-2026-000042")
        self._create_case(number="TRV-LY-2026-000043")

        response = self.client.get("/api/v1/travel-cases/?search=000042")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([row["case_number"] for row in response.data], [match.case_number])

    def _create_user(self, username, group_name):
        user = self.user_model.objects.create_user(username=username, password="test-pass")
        group, _ = Group.objects.get_or_create(name=group_name)
        user.groups.add(group)
        return user

    def _create_case(self, status_value=TravelCaseStatus.UNDER_BOOKING, number="TRV-LY-2026-000001"):
        return TravelCase.objects.create(
            case_number=number,
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
            created_by=self.user,
        )
