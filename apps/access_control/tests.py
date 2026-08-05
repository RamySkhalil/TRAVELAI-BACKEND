from datetime import date

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.test import APIClient

from apps.audit_logs.models import AuditLog
from apps.master_data.models import Country, Department, Employee, Project, Route, Supplier
from apps.travel_cases.models import AccountType, TravelCase, TravelPurpose

from apps.access_control.models import ScopeType, TravelOpsUserProfile, UserAccessScope
from apps.access_control.permissions import apply_scope_filter, is_super_admin

User = get_user_model()

USERS_URL = "/api/v1/access-control/users/"
SCOPES_URL = "/api/v1/access-control/scopes/"
ROLES_URL = "/api/v1/access-control/roles/"
ADMIN_SETTINGS_SUMMARY_URL = "/api/v1/admin-settings/summary/"


class AccessControlSetupMixin:
    def _master_data(self):
        self.country = Country.objects.create(code="LY", name="Libya")
        self.department = Department.objects.create(code="OPS", name="Operations")
        self.project_a = Project.objects.create(code="PRJ-A", name="Project A", country=self.country)
        self.project_b = Project.objects.create(code="PRJ-B", name="Project B", country=self.country)
        self.employee_a = Employee.objects.create(
            badge_number="E-A", full_name="Emp A", project=self.project_a, department=self.department
        )
        self.employee_b = Employee.objects.create(
            badge_number="E-B", full_name="Emp B", project=self.project_b, department=self.department
        )

    def _travel_case(self, *, number, project, employee, created_by=None, assigned_to=None):
        return TravelCase.objects.create(
            case_number=number,
            employee=employee,
            badge_number=employee.badge_number,
            employee_name=employee.full_name,
            project=project,
            department=self.department,
            country=self.country,
            travel_purpose=TravelPurpose.BUSINESS_TRIP,
            account_type=AccountType.COMPANY,
            route_from="TIP",
            route_to="IST",
            requested_travel_date=date(2026, 7, 1),
            created_by=created_by,
            assigned_to=assigned_to,
        )

    def _user(self, username, group=None, **flags):
        user = User.objects.create_user(username=username, password="pw-Travel-123", **flags)
        if group:
            grp, _ = Group.objects.get_or_create(name=group)
            user.groups.add(grp)
        return user


class SuperAdminBehaviorTests(AccessControlSetupMixin, TestCase):
    def test_django_superuser_is_treated_as_super_admin(self):
        superuser = User.objects.create_superuser(username="root", password="pw-Travel-123")
        self.assertTrue(is_super_admin(superuser))

    def test_super_admin_group_user_has_full_access(self):
        user = self._user("sa", group="SuperAdmin")
        self.assertTrue(is_super_admin(user))

        self._master_data()
        self._travel_case(number="TRV-1", project=self.project_a, employee=self.employee_a)
        UserAccessScope.objects.create(user=user, scope_type=ScopeType.PROJECT, project=self.project_b)

        # Super Admin bypasses scope filtering even with a narrow scope configured.
        visible = apply_scope_filter(TravelCase.objects.all(), user, "travel_case")
        self.assertEqual(visible.count(), 1)

    def test_profile_flag_grants_super_admin(self):
        user = self._user("flagged")
        TravelOpsUserProfile.objects.create(user=user, is_super_admin=True)
        self.assertTrue(is_super_admin(user))


class UserManagementApiTests(AccessControlSetupMixin, TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = self._user("admin1", group="Admin", is_staff=True)

    def test_admin_can_list_users(self):
        self.client.force_authenticate(self.admin)
        response = self.client.get(USERS_URL)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_non_admin_cannot_list_users(self):
        hr_user = self._user("hr1", group="HR")
        self.client.force_authenticate(hr_user)
        response = self.client.get(USERS_URL)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_unauthenticated_user_cannot_view_user_detail(self):
        target = self._user("detail-target")
        response = self.client.get(f"{USERS_URL}{target.id}/")
        self.assertIn(response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))

    def test_non_admin_cannot_view_user_detail(self):
        hr_user = self._user("hr-detail", group="HR")
        target = self._user("detail-target-2")
        self.client.force_authenticate(hr_user)
        response = self.client.get(f"{USERS_URL}{target.id}/")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_can_view_normal_user_detail(self):
        self.client.force_authenticate(self.admin)
        target = self._user("normal-detail")
        response = self.client.get(f"{USERS_URL}{target.id}/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["username"], target.username)
        self.assertNotIn("password", response.data)

    def test_superadmin_can_view_all_users(self):
        superadmin = self._user("super-detail", group="SuperAdmin")
        target = self._user("target-detail", group="SuperAdmin")
        self.client.force_authenticate(superadmin)
        response = self.client.get(f"{USERS_URL}{target.id}/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_create_user_returns_generated_password_once_and_never_again(self):
        self.client.force_authenticate(self.admin)
        response = self.client.post(
            USERS_URL,
            {"username": "newbie", "generate_password": True, "roles": ["HR"]},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertIn("generated_password", response.data)
        self.assertTrue(response.data["generated_password"])
        created_id = response.data["id"]

        detail = self.client.get(f"{USERS_URL}{created_id}/")
        self.assertEqual(detail.status_code, status.HTTP_200_OK)
        self.assertNotIn("generated_password", detail.data)
        self.assertNotIn("password", detail.data)

        listing = self.client.get(USERS_URL)
        for row in listing.data:
            self.assertNotIn("password", row)
            self.assertNotIn("generated_password", row)

    def test_create_user_requires_password_or_generation(self):
        self.client.force_authenticate(self.admin)
        response = self.client.post(USERS_URL, {"username": "nopass"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_admin_cannot_grant_super_admin(self):
        self.client.force_authenticate(self.admin)
        response = self.client.post(
            USERS_URL,
            {"username": "wannabe", "generate_password": True, "roles": ["SuperAdmin"]},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_can_update_normal_user_and_audits(self):
        self.client.force_authenticate(self.admin)
        target = self._user("update-target")
        response = self.client.patch(
            f"{USERS_URL}{target.id}/",
            {"first_name": "Updated", "last_name": "User", "email": "updated@example.com", "job_title": "Lead"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        target.refresh_from_db()
        self.assertEqual(target.first_name, "Updated")
        self.assertEqual(target.travelops_profile.job_title, "Lead")
        self.assertTrue(AuditLog.objects.filter(action="User Updated", entity_id=str(target.id)).exists())

    def test_non_admin_cannot_update_user(self):
        hr_user = self._user("hr-update", group="HR")
        target = self._user("update-forbidden")
        self.client.force_authenticate(hr_user)
        response = self.client.patch(f"{USERS_URL}{target.id}/", {"first_name": "Nope"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_cannot_assign_super_admin_role(self):
        self.client.force_authenticate(self.admin)
        target = self._user("role-target")
        response = self.client.post(
            f"{USERS_URL}{target.id}/assign-roles/",
            {"roles": ["SuperAdmin"]},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_role_assignment_works_and_audits(self):
        self.client.force_authenticate(self.admin)
        target = self._user("target")
        response = self.client.post(
            f"{USERS_URL}{target.id}/assign-roles/",
            {"roles": ["HR", "Finance"]},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertCountEqual(list(target.groups.values_list("name", flat=True)), ["HR", "Finance"])
        self.assertTrue(AuditLog.objects.filter(action="Role Assigned", entity_id=str(target.id)).exists())

    def test_disable_user(self):
        self.client.force_authenticate(self.admin)
        target = self._user("to-disable")
        response = self.client.post(f"{USERS_URL}{target.id}/disable/", format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        target.refresh_from_db()
        self.assertFalse(target.is_active)
        self.assertTrue(AuditLog.objects.filter(action="User Disabled", entity_id=str(target.id)).exists())

    def test_password_reset_changes_password_using_django_hashing_and_audits_safely(self):
        self.client.force_authenticate(self.admin)
        target = self._user("password-target")
        response = self.client.post(
            f"{USERS_URL}{target.id}/reset-password/",
            {"password": "New-Travel-Password-123"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        target.refresh_from_db()
        self.assertTrue(target.check_password("New-Travel-Password-123"))
        self.assertNotEqual(target.password, "New-Travel-Password-123")
        audit = AuditLog.objects.get(action="Password Reset", entity_id=str(target.id))
        self.assertNotIn("New-Travel-Password-123", str(audit.new_value))
        self.assertNotIn("New-Travel-Password-123", str(audit.metadata))

    def test_generated_password_reset_is_returned_once_only(self):
        self.client.force_authenticate(self.admin)
        target = self._user("generated-target")
        response = self.client.post(f"{USERS_URL}{target.id}/generate-password/", format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        generated_password = response.data["generated_password"]
        self.assertTrue(generated_password)
        target.refresh_from_db()
        self.assertTrue(target.check_password(generated_password))
        self.assertTrue(AuditLog.objects.filter(action="Generated Password Reset", entity_id=str(target.id)).exists())

        detail = self.client.get(f"{USERS_URL}{target.id}/")
        self.assertEqual(detail.status_code, status.HTTP_200_OK)
        self.assertNotIn("generated_password", detail.data)

    def test_activate_user_works_and_audits(self):
        self.client.force_authenticate(self.admin)
        target = self._user("to-activate")
        target.is_active = False
        target.save(update_fields=["is_active"])
        response = self.client.post(f"{USERS_URL}{target.id}/activate/", format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        target.refresh_from_db()
        self.assertTrue(target.is_active)
        self.assertTrue(AuditLog.objects.filter(action="User Activated", entity_id=str(target.id)).exists())

    def test_deactivate_user_works(self):
        self.client.force_authenticate(self.admin)
        target = self._user("to-deactivate")
        response = self.client.post(f"{USERS_URL}{target.id}/deactivate/", format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        target.refresh_from_db()
        self.assertFalse(target.is_active)

    def test_user_cannot_deactivate_self(self):
        self.client.force_authenticate(self.admin)
        response = self.client.post(f"{USERS_URL}{self.admin.id}/deactivate/", format="json")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_cannot_deactivate_superadmin_unless_superadmin(self):
        self.client.force_authenticate(self.admin)
        target = self._user("protected-super", group="SuperAdmin")
        response = self.client.post(f"{USERS_URL}{target.id}/deactivate/", format="json")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        superadmin = self._user("allowed-super", group="SuperAdmin")
        self.client.force_authenticate(superadmin)
        response = self.client.post(f"{USERS_URL}{target.id}/deactivate/", format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_audit_logs_are_created_for_user_management_events(self):
        superadmin = self._user("audit-super", group="SuperAdmin")
        self.client.force_authenticate(superadmin)
        target = self._user("audit-target", group="HR")
        self.client.patch(f"{USERS_URL}{target.id}/", {"first_name": "Audit"}, format="json")
        self.client.post(f"{USERS_URL}{target.id}/assign-roles/", {"roles": ["Finance"]}, format="json")
        self.client.post(f"{USERS_URL}{target.id}/reset-password/", {"password": "Audit-Password-123"}, format="json")
        self.client.post(f"{USERS_URL}{target.id}/deactivate/", format="json")
        self.client.post(f"{USERS_URL}{target.id}/activate/", format="json")
        actions = set(AuditLog.objects.filter(entity_id=str(target.id)).values_list("action", flat=True))
        self.assertTrue(
            {
                "User Updated",
                "Role Assigned",
                "Role Removed",
                "Password Reset",
                "User Disabled",
                "User Activated",
            }.issubset(actions)
        )

    def test_available_roles_endpoint(self):
        self.client.force_authenticate(self.admin)
        response = self.client.get(ROLES_URL)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        names = [row["name"] for row in response.data]
        self.assertIn("SuperAdmin", names)
        self.assertIn("Auditor", names)


class ScopeApiTests(AccessControlSetupMixin, TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = self._user("admin2", group="Admin", is_staff=True)
        self._master_data()

    def test_scope_creation_works_and_audits(self):
        self.client.force_authenticate(self.admin)
        target = self._user("scoped")
        response = self.client.post(
            SCOPES_URL,
            {"user": target.id, "scope_type": ScopeType.PROJECT, "project": self.project_a.id},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(UserAccessScope.objects.filter(user=target, project=self.project_a).exists())
        self.assertTrue(AuditLog.objects.filter(action="Scope Created").exists())

    def test_scope_requires_target_for_typed_scope(self):
        self.client.force_authenticate(self.admin)
        target = self._user("scoped2")
        response = self.client.post(
            SCOPES_URL,
            {"user": target.id, "scope_type": ScopeType.PROJECT},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_scope_deactivation_keeps_record_and_audits(self):
        self.client.force_authenticate(self.admin)
        target = self._user("scoped3")
        scope = UserAccessScope.objects.create(user=target, scope_type=ScopeType.PROJECT, project=self.project_a)
        response = self.client.delete(f"{SCOPES_URL}{scope.id}/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        scope.refresh_from_db()
        self.assertFalse(scope.is_active)
        self.assertTrue(AuditLog.objects.filter(action="Scope Deactivated").exists())


class ScopeFilterTests(AccessControlSetupMixin, TestCase):
    def setUp(self):
        self.client = APIClient()
        self._master_data()
        self.scoped_user = self._user("viewer")
        self.case_a = self._travel_case(number="TRV-A", project=self.project_a, employee=self.employee_a)
        self.case_b = self._travel_case(number="TRV-B", project=self.project_b, employee=self.employee_b)

    def test_project_scope_limits_visibility(self):
        UserAccessScope.objects.create(user=self.scoped_user, scope_type=ScopeType.PROJECT, project=self.project_a)
        self.client.force_authenticate(self.scoped_user)
        response = self.client.get("/api/v1/travel-cases/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        numbers = {row["case_number"] for row in response.data}
        self.assertEqual(numbers, {"TRV-A"})

    def test_assigned_to_me_case_remains_visible_under_scope(self):
        UserAccessScope.objects.create(user=self.scoped_user, scope_type=ScopeType.PROJECT, project=self.project_a)
        self.case_b.assigned_to = self.scoped_user
        self.case_b.save(update_fields=["assigned_to"])
        self.client.force_authenticate(self.scoped_user)
        response = self.client.get("/api/v1/travel-cases/")
        numbers = {row["case_number"] for row in response.data}
        self.assertEqual(numbers, {"TRV-A", "TRV-B"})

    def test_user_without_scopes_sees_everything(self):
        self.client.force_authenticate(self.scoped_user)
        response = self.client.get("/api/v1/travel-cases/")
        self.assertEqual(len(response.data), 2)

    def test_global_scope_sees_everything(self):
        UserAccessScope.objects.create(user=self.scoped_user, scope_type=ScopeType.GLOBAL)
        self.client.force_authenticate(self.scoped_user)
        response = self.client.get("/api/v1/travel-cases/")
        self.assertEqual(len(response.data), 2)


class AuditorTests(AccessControlSetupMixin, TestCase):
    def setUp(self):
        self.client = APIClient()
        self._master_data()
        self.auditor = self._user("auditor1", group="Auditor")

    def test_auditor_can_read_but_cannot_mutate_travel_cases(self):
        self.case = self._travel_case(number="TRV-AUD", project=self.project_a, employee=self.employee_a)
        self.client.force_authenticate(self.auditor)

        read = self.client.get("/api/v1/travel-cases/")
        self.assertEqual(read.status_code, status.HTTP_200_OK)

        write = self.client.post(
            "/api/v1/travel-cases/",
            {
                "employee": self.employee_a.id,
                "project": self.project_a.id,
                "department": self.department.id,
                "country": self.country.id,
                "travel_purpose": TravelPurpose.BUSINESS_TRIP,
                "account_type": AccountType.COMPANY,
                "route_from": "TIP",
                "route_to": "IST",
                "requested_travel_date": "2026-07-01",
            },
            format="json",
        )
        self.assertEqual(write.status_code, status.HTTP_403_FORBIDDEN)


class AdminSettingsSummaryApiTests(AccessControlSetupMixin, TestCase):
    def setUp(self):
        self.client = APIClient()
        self._master_data()
        self.admin = self._user("admin-settings-admin", group="Admin", is_staff=True)
        self.superadmin = self._user("admin-settings-super", group="SuperAdmin", is_staff=True)
        self.hr_user = self._user("admin-settings-hr", group="HR")
        self.supplier = Supplier.objects.create(code="SUP-A", name="Supplier A")
        self.route = Route.objects.create(origin="TIP", destination="IST", country=self.country)
        UserAccessScope.objects.create(user=self.hr_user, scope_type=ScopeType.PROJECT, project=self.project_a)
        AuditLog.objects.create(user=self.admin, action="Role Assigned", entity_type="User", entity_id=str(self.hr_user.id))

    def test_unauthenticated_user_cannot_access_admin_settings_summary(self):
        response = self.client.get(ADMIN_SETTINGS_SUMMARY_URL)
        self.assertIn(response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))

    def test_non_admin_user_cannot_access_admin_settings_summary(self):
        self.client.force_authenticate(self.hr_user)
        response = self.client.get(ADMIN_SETTINGS_SUMMARY_URL)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_can_access_admin_settings_summary(self):
        self.client.force_authenticate(self.admin)
        response = self.client.get(ADMIN_SETTINGS_SUMMARY_URL)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["users"]["manage_users_url"], "/admin/users")

    def test_superadmin_can_access_admin_settings_summary(self):
        self.client.force_authenticate(self.superadmin)
        response = self.client.get(ADMIN_SETTINGS_SUMMARY_URL)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["current_user"]["is_super_admin"])

    def test_admin_settings_summary_does_not_expose_secret_values_or_names(self):
        self.client.force_authenticate(self.admin)
        response = self.client.get(ADMIN_SETTINGS_SUMMARY_URL)
        content = response.rendered_content.decode("utf-8")
        forbidden_fragments = (
            "DATABASE_URL",
            "OPENAI_API_KEY",
            "CLOUDFLARE_R2_SECRET_ACCESS_KEY",
            "SECRET_KEY",
            "password",
            "token",
        )
        for fragment in forbidden_fragments:
            self.assertNotIn(fragment, content)

    def test_admin_settings_summary_includes_user_and_role_counts(self):
        self.client.force_authenticate(self.admin)
        response = self.client.get(ADMIN_SETTINGS_SUMMARY_URL)
        self.assertGreaterEqual(response.data["users"]["total"], 3)
        self.assertGreaterEqual(response.data["users"]["active"], 3)
        self.assertEqual(response.data["users"]["inactive"], 0)
        by_role = {row["role"]: row["count"] for row in response.data["users"]["by_role"]}
        self.assertEqual(by_role["Admin"], 1)
        self.assertEqual(by_role["HR"], 1)

        roles = {row["name"]: row for row in response.data["roles"]}
        self.assertEqual(roles["Admin"]["user_count"], 1)
        self.assertIn("allowed_business_area", roles["Finance"])

    def test_admin_settings_summary_includes_master_data_counts(self):
        self.client.force_authenticate(self.admin)
        response = self.client.get(ADMIN_SETTINGS_SUMMARY_URL)
        self.assertEqual(response.data["master_data"]["countries"], 1)
        self.assertEqual(response.data["master_data"]["projects"], 2)
        self.assertEqual(response.data["master_data"]["departments"], 1)
        self.assertEqual(response.data["master_data"]["suppliers"], 1)
        self.assertEqual(response.data["master_data"]["routes"], 1)
        self.assertEqual(response.data["master_data"]["employees"], 2)

    def test_admin_settings_summary_includes_access_scope_and_audit_summary(self):
        self.client.force_authenticate(self.admin)
        response = self.client.get(ADMIN_SETTINGS_SUMMARY_URL)
        self.assertTrue(response.data["access_scopes"]["enabled"])
        self.assertEqual(response.data["access_scopes"]["management_url"], "/admin/access-scopes")
        self.assertGreaterEqual(response.data["audit"]["total_count"], 1)
        self.assertEqual(response.data["audit"]["recent_admin_security_count"], 1)


class SeedDemoUsersTests(TestCase):
    @override_settings(DEBUG=True)
    def test_seed_demo_users_creates_superadmin_demo_and_group(self):
        call_command("seed_demo_users", verbosity=0)
        self.assertTrue(Group.objects.filter(name="SuperAdmin").exists())
        superadmin = User.objects.get(username="superadmin_demo")
        self.assertTrue(superadmin.is_superuser)
        self.assertTrue(superadmin.groups.filter(name="SuperAdmin").exists())
        self.assertTrue(superadmin.travelops_profile.is_super_admin)
        for username in ("admin_demo", "hr_demo", "booking_demo", "booking_manager_demo", "finance_demo", "auditor_demo"):
            self.assertTrue(User.objects.filter(username=username).exists())
