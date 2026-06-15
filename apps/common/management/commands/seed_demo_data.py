from datetime import date

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.common.models import ControlSequence
from apps.master_data.models import Country, Department, Employee, Project, Route, Supplier
from apps.permits.models import Permit, PermitStatus, PermitType
from apps.ticket_versions.models import TicketAction, TicketStatus, TicketVersion
from apps.travel_cases.models import AccountType, Priority, TravelCase, TravelPurpose
from apps.travel_cases.services import create_travel_case


class Command(BaseCommand):
    help = "Seed local development master data and travel cases with demo-only names."

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError("Refusing to seed demo data when DJANGO_DEBUG is not True.")

        with transaction.atomic():
            hr_group, _ = Group.objects.get_or_create(name="HR")
            user, _ = get_user_model().objects.get_or_create(
                username="demo_data_seed",
                defaults={
                    "email": "demo-data-seed@demo.local",
                    "first_name": "Demo",
                    "last_name": "Seeder",
                },
            )
            if not user.has_usable_password():
                user.set_unusable_password()
                user.save(update_fields=["password"])
            user.groups.add(hr_group)

            libya, _ = Country.objects.update_or_create(
                code="LY",
                defaults={"name": "Libya", "is_active": True},
            )
            operations, _ = Department.objects.update_or_create(
                code="DEMO-OPS",
                defaults={"name": "Demo Operations", "is_active": True},
            )
            logistics, _ = Department.objects.update_or_create(
                code="DEMO-LOG",
                defaults={"name": "Demo Logistics", "is_active": True},
            )
            sirte, _ = Project.objects.update_or_create(
                code="DEMO-SIRTE",
                defaults={"name": "Demo Sirte Field Ops", "country": libya, "cost_center": "DEMO-CC-001", "is_active": True},
            )
            tripoli, _ = Project.objects.update_or_create(
                code="DEMO-TRIPOLI",
                defaults={"name": "Demo Tripoli HQ", "country": libya, "cost_center": "DEMO-CC-002", "is_active": True},
            )
            supplier, _ = Supplier.objects.update_or_create(
                code="DEMO-AIR",
                defaults={"name": "Demo Air Supplier", "contact_name": "Demo Contact", "email": "supplier@demo.local", "is_active": True},
            )

            for origin, destination in (("TIP", "IST"), ("TIP", "TUN"), ("BEN", "TIP")):
                Route.objects.get_or_create(origin=origin, destination=destination, country=libya, defaults={"is_active": True})

            employees = [
                Employee.objects.update_or_create(
                    badge_number="DEMO-EMP-001",
                    defaults={
                        "full_name": "Omar Demo",
                        "project": sirte,
                        "department": operations,
                        "job_title": "Field Engineer",
                        "email": "omar.demo@demo.local",
                        "is_active": True,
                    },
                )[0],
                Employee.objects.update_or_create(
                    badge_number="DEMO-EMP-002",
                    defaults={
                        "full_name": "Layla Demo",
                        "project": tripoli,
                        "department": logistics,
                        "job_title": "Travel Coordinator",
                        "email": "layla.demo@demo.local",
                        "is_active": True,
                    },
                )[0],
                Employee.objects.update_or_create(
                    badge_number="DEMO-EMP-003",
                    defaults={
                        "full_name": "Yousef Demo",
                        "project": sirte,
                        "department": logistics,
                        "job_title": "Logistics Supervisor",
                        "email": "yousef.demo@demo.local",
                        "is_active": True,
                    },
                )[0],
            ]

            self._reconcile_travel_case_sequence("TRV-LY", 2026)

            cases = [
                self._get_or_create_case(
                    employee=employees[0],
                    project=sirte,
                    department=operations,
                    country=libya,
                    route_from="TIP",
                    route_to="IST",
                    requested_travel_date=date(2026, 6, 24),
                    requested_return_date=date(2026, 6, 30),
                    priority=Priority.HIGH,
                    user=user,
                ),
                self._get_or_create_case(
                    employee=employees[1],
                    project=tripoli,
                    department=logistics,
                    country=libya,
                    route_from="TIP",
                    route_to="TUN",
                    requested_travel_date=date(2026, 6, 26),
                    requested_return_date=None,
                    priority=Priority.NORMAL,
                    user=user,
                ),
                self._get_or_create_case(
                    employee=employees[2],
                    project=sirte,
                    department=logistics,
                    country=libya,
                    route_from="BEN",
                    route_to="TIP",
                    requested_travel_date=date(2026, 6, 28),
                    requested_return_date=date(2026, 7, 4),
                    priority=Priority.LOW,
                    user=user,
                ),
            ]

            TicketVersion.objects.get_or_create(
                travel_case=cases[0],
                version_number="V1",
                defaults={
                    "ticket_action": TicketAction.ORIGINAL,
                    "passenger_name": cases[0].employee_name,
                    "ticket_number": "DEMO-074-000001",
                    "pnr": "DMO1LY",
                    "airline": "Demo Air",
                    "route_from": cases[0].route_from,
                    "route_to": cases[0].route_to,
                    "departure_date": cases[0].requested_travel_date,
                    "amount": "1180.00",
                    "currency": "USD",
                    "supplier": supplier,
                    "ticket_status": TicketStatus.ACTIVE,
                    "confirmed_by": user,
                },
            )
            Permit.objects.get_or_create(
                travel_case=cases[0],
                permit_type=PermitType.LIBYA_PERMIT,
                defaults={
                    "status": PermitStatus.PENDING,
                    "issue_date": None,
                    "expiry_date": date(2026, 12, 31),
                    "notes": "Demo local permit record.",
                    "created_by": user,
                },
            )

        self.stdout.write(self.style.SUCCESS("Seeded local demo master data and travel cases."))

    def _reconcile_travel_case_sequence(self, code: str, year: int):
        prefix = f"{code}-{year}-"
        max_number = 0
        for case_number in TravelCase.objects.filter(case_number__startswith=prefix).values_list("case_number", flat=True):
            try:
                max_number = max(max_number, int(case_number.rsplit("-", 1)[1]))
            except (IndexError, ValueError):
                continue
        sequence, _ = ControlSequence.objects.get_or_create(code=code, year=year)
        if sequence.last_number < max_number:
            sequence.last_number = max_number
            sequence.save(update_fields=["last_number", "updated_at"])

    def _get_or_create_case(self, **data):
        user = data.pop("user")
        existing = (
            data["employee"]
            .travel_cases.filter(
                route_from=data["route_from"],
                route_to=data["route_to"],
                requested_travel_date=data["requested_travel_date"],
                notes__contains="seed_demo_data",
            )
            .first()
        )
        if existing:
            return existing
        return create_travel_case(
            {
                **data,
                "travel_purpose": TravelPurpose.BUSINESS_TRIP,
                "account_type": AccountType.COMPANY,
                "approval_required": True,
                "notes": "Local demo record created by seed_demo_data.",
            },
            user,
        )
