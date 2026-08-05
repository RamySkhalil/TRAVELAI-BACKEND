import tempfile
from datetime import date
from pathlib import Path

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from apps.audit_logs.models import AuditLog
from apps.travel_cases.models import AccountType, TravelCase, TravelPurpose

from .models import Country, Department, Employee, Project


EMPLOYEES_URL = "/api/v1/employees/"


class EmployeeMasterDataApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = self._user("employee-admin", group="Admin", is_staff=True)
        self.hr_user = self._user("employee-hr", group="HR")
        self.viewer = self._user("employee-viewer", group="Finance")
        self.country = Country.objects.create(code="LY", name="Libya")
        self.project = Project.objects.create(code="PRJ", name="Project", country=self.country)
        self.department = Department.objects.create(code="OPS", name="Operations")

    def _user(self, username, *, group=None, is_staff=False):
        user = get_user_model().objects.create_user(username=username, password="test-pass", is_staff=is_staff)
        if group:
            group_obj, _ = Group.objects.get_or_create(name=group)
            user.groups.add(group_obj)
        return user

    def _payload(self, **overrides):
        payload = {
            "badge_number": "EMP-001",
            "full_name": "Aisha Demo",
            "project": self.project.id,
            "department": self.department.id,
            "job_title": "Travel Coordinator",
            "email": "aisha.demo@example.com",
            "phone": "+201000000000",
            "notes": "Demo employee record.",
            "is_active": True,
        }
        payload.update(overrides)
        return payload

    def _employee(self, **overrides):
        data = self._payload(**overrides)
        data["project"] = self.project
        data["department"] = self.department
        return Employee.objects.create(**data)

    def test_employee_endpoints_require_authentication(self):
        response = self.client.get(EMPLOYEES_URL)

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_admin_can_create_employee_and_audit_log_is_created(self):
        self.client.force_authenticate(self.admin)

        response = self.client.post(EMPLOYEES_URL, self._payload(), format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["badge_number"], "EMP-001")
        self.assertEqual(response.data["country"], self.country.id)
        self.assertTrue(AuditLog.objects.filter(action="Employee Created", entity_type="Employee").exists())

    def test_non_admin_cannot_create_employee(self):
        self.client.force_authenticate(self.hr_user)

        response = self.client.post(EMPLOYEES_URL, self._payload(), format="json")

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_badge_number_must_be_unique(self):
        self._employee()
        self.client.force_authenticate(self.admin)

        response = self.client.post(EMPLOYEES_URL, self._payload(full_name="Duplicate Badge"), format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("badge_number", response.data)

    def test_employee_can_be_updated_and_audit_log_is_created(self):
        employee = self._employee()
        self.client.force_authenticate(self.admin)

        response = self.client.patch(f"{EMPLOYEES_URL}{employee.id}/", {"job_title": "Lead Coordinator"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        employee.refresh_from_db()
        self.assertEqual(employee.job_title, "Lead Coordinator")
        self.assertTrue(AuditLog.objects.filter(action="Employee Updated", entity_id=str(employee.id)).exists())

    def test_employee_can_be_deactivated_and_reactivated(self):
        employee = self._employee()
        self.client.force_authenticate(self.admin)

        deactivate = self.client.post(f"{EMPLOYEES_URL}{employee.id}/deactivate/", format="json")
        activate = self.client.post(f"{EMPLOYEES_URL}{employee.id}/activate/", format="json")

        self.assertEqual(deactivate.status_code, status.HTTP_200_OK)
        self.assertFalse(deactivate.data["is_active"])
        self.assertEqual(activate.status_code, status.HTTP_200_OK)
        self.assertTrue(activate.data["is_active"])
        actions = set(AuditLog.objects.filter(entity_id=str(employee.id)).values_list("action", flat=True))
        self.assertIn("Employee Deactivated", actions)
        self.assertIn("Employee Activated", actions)

    def test_active_filter_excludes_deactivated_employees(self):
        active = self._employee(badge_number="EMP-ACTIVE", full_name="Active Employee", is_active=True)
        inactive = self._employee(badge_number="EMP-INACTIVE", full_name="Inactive Employee", is_active=False)
        self.client.force_authenticate(self.viewer)

        response = self.client.get(f"{EMPLOYEES_URL}?is_active=true")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = {row["id"] for row in response.data}
        self.assertIn(active.id, ids)
        self.assertNotIn(inactive.id, ids)

    def test_existing_travel_case_keeps_employee_snapshot_after_deactivation(self):
        employee = self._employee(full_name="Snapshot Name", badge_number="SNAP-001")
        travel_case = TravelCase.objects.create(
            case_number="TRV-LY-2026-000999",
            employee=employee,
            badge_number=employee.badge_number,
            employee_name=employee.full_name,
            project=self.project,
            department=self.department,
            country=self.country,
            travel_purpose=TravelPurpose.BUSINESS_TRIP,
            account_type=AccountType.COMPANY,
            route_from="TIP",
            route_to="IST",
            requested_travel_date=date(2026, 6, 30),
        )
        employee.full_name = "Changed Name"
        employee.is_active = False
        employee.save(update_fields=["full_name", "is_active", "updated_at"])

        travel_case.refresh_from_db()

        self.assertEqual(travel_case.employee_name, "Snapshot Name")
        self.assertEqual(travel_case.badge_number, "SNAP-001")

    def test_inactive_employee_cannot_be_used_for_new_travel_case(self):
        employee = self._employee(is_active=False)
        self.client.force_authenticate(self.admin)

        response = self.client.post(
            "/api/v1/travel-cases/",
            {
                "employee": employee.id,
                "project": self.project.id,
                "department": self.department.id,
                "country": self.country.id,
                "travel_purpose": TravelPurpose.BUSINESS_TRIP,
                "account_type": AccountType.COMPANY,
                "route_from": "TIP",
                "route_to": "IST",
                "requested_travel_date": "2026-06-30",
                "approval_required": False,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_schema_generation_still_works(self):
        with tempfile.TemporaryDirectory() as schema_dir:
            call_command("spectacular", file=str(Path(schema_dir) / "schema.yml"), verbosity=0)
