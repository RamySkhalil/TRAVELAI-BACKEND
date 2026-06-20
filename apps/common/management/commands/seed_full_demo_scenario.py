"""Seed a complete, realistic, idempotent demo workflow for UI walkthroughs.

This command builds the full controlled chain (travel case -> ticket -> supplier
invoice -> matching -> HR approval -> TBCN -> finance -> paid) at several stages so
the UI shows useful data on every screen.

Safety rules:
- DEBUG-only. Refuses to run in production-like settings.
- Idempotent. Safe to rerun; existing demo chains are detected and skipped.
- Never flushes or deletes existing data.
- Uses fake demo names only and never touches secrets.
"""
from datetime import date

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError, transaction

from apps.ai_extraction.models import DocumentExtractionJob, DocumentType, ExtractionStatus
from apps.billing_confirmations.services import (
    generate_tbcn,
    generate_tbcn_pdf,
    mark_finance_accepted,
    mark_tbcn_paid,
    send_tbcn_to_finance,
)
from apps.common.models import ControlSequence
from apps.invoice_matching.services import match_invoice
from apps.master_data.models import Country, Department, Employee, Project, Route, Supplier
from apps.permits.models import Permit, PermitStatus, PermitType
from apps.supplier_invoices.services import approve_supplier_invoice, create_supplier_invoice_from_confirmed_extraction
from apps.ticket_versions.models import TicketAction
from apps.ticket_versions.services import confirm_ticket_version, create_ticket_version_from_confirmed_extraction
from apps.travel_cases.models import AccountType, Priority, TravelCase, TravelPurpose
from apps.travel_cases.services import assign_travel_case, create_travel_case, submit_travel_case

MARKER_PREFIX = "seed_full_demo_scenario"

# stage progression order; each scenario stops at its target stage.
STAGE_SUBMITTED = "submitted"
STAGE_TICKETED = "ticketed"
STAGE_MATCHING = "matching"
STAGE_TBCN_READY = "tbcn_ready"
STAGE_TBCN_GENERATED = "tbcn_generated"
STAGE_FINANCE_SENT = "finance_sent"
STAGE_PAID = "paid"

SCENARIOS = [
    {"key": STAGE_SUBMITTED, "stage": STAGE_SUBMITTED, "route": ("TIP", "IST"), "amount": "980.00", "currency": "USD", "travel_date": date(2026, 8, 3)},
    {"key": STAGE_TICKETED, "stage": STAGE_TICKETED, "route": ("TIP", "TUN"), "amount": "1120.00", "currency": "USD", "travel_date": date(2026, 8, 5)},
    {"key": STAGE_MATCHING, "stage": STAGE_MATCHING, "route": ("BEN", "TIP"), "amount": "1340.00", "currency": "USD", "travel_date": date(2026, 8, 7)},
    {"key": STAGE_TBCN_READY, "stage": STAGE_TBCN_READY, "route": ("TIP", "CAI"), "amount": "1560.00", "currency": "USD", "travel_date": date(2026, 8, 9)},
    {"key": STAGE_TBCN_GENERATED, "stage": STAGE_TBCN_GENERATED, "route": ("CAI", "HBE"), "amount": "18500.00", "currency": "EGP", "travel_date": date(2026, 8, 11)},
    {"key": STAGE_FINANCE_SENT, "stage": STAGE_FINANCE_SENT, "route": ("CAI", "ALY"), "amount": "24250.00", "currency": "EGP", "travel_date": date(2026, 8, 13)},
    {"key": STAGE_PAID, "stage": STAGE_PAID, "route": ("TIP", "TUN"), "amount": "1480.00", "currency": "USD", "travel_date": date(2026, 8, 15)},
]


class Command(BaseCommand):
    help = "Seed a full, idempotent demo workflow chain across all stages (DEBUG only)."

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError("Refusing to seed the full demo scenario when DJANGO_DEBUG is not True.")

        call_command("seed_demo_users", verbosity=0)
        master = self._ensure_master_data()
        self._reconcile_travel_case_sequence("TRV-LY", 2026)

        created = 0
        skipped = 0
        for index, scenario in enumerate(SCENARIOS):
            employee = master["employees"][index % len(master["employees"])]
            project = master["projects"][index % len(master["projects"])]
            department = master["departments"][index % len(master["departments"])]
            supplier = master["suppliers"][index % len(master["suppliers"])]
            result = self._build_chain(
                scenario=scenario,
                employee=employee,
                project=project,
                department=department,
                supplier=supplier,
                country=master["country"],
                users=master["users"],
            )
            if result:
                created += 1
            else:
                skipped += 1

        self._ensure_permits(master["users"]["hr"])

        self.stdout.write(self.style.SUCCESS(f"Full demo scenario ready. Chains created: {created}, already present: {skipped}."))
        self.stdout.write("Demo logins are managed by seed_demo_users (DEVELOPMENT ONLY).")

    # ----- master data -------------------------------------------------

    def _ensure_master_data(self):
        user_model = get_user_model()
        users = {
            "hr": self._group_user(user_model, "hr_demo", "HR"),
            "booking": self._group_user(user_model, "booking_demo", "BookingOfficer"),
            "finance": self._group_user(user_model, "finance_demo", "Finance"),
        }

        country = self._ensure_record(Country, {"code": "LY"}, {"name": "Libya", "is_active": True})
        departments = [
            self._ensure_record(Department, {"code": "FD-OPS"}, {"name": "Demo Field Operations", "is_active": True}),
            self._ensure_record(Department, {"code": "FD-LOG"}, {"name": "Demo Logistics", "is_active": True}),
        ]
        projects = [
            self._ensure_record(Project, {"code": "FD-NORTH"}, {"name": "Demo North Field", "country": country, "cost_center": "FD-CC-100", "is_active": True}),
            self._ensure_record(Project, {"code": "FD-SOUTH"}, {"name": "Demo South Field", "country": country, "cost_center": "FD-CC-200", "is_active": True}),
        ]
        suppliers = [
            self._ensure_record(Supplier, {"code": "FD-WINGS"}, {"name": "Demo Wings Air", "contact_name": "Demo Desk", "email": "wings@demo.local", "is_active": True}),
            self._ensure_record(Supplier, {"code": "FD-SKY"}, {"name": "Demo Sky Travel", "contact_name": "Demo Desk", "email": "sky@demo.local", "is_active": True}),
        ]
        for origin, destination in (("TIP", "IST"), ("TIP", "TUN"), ("BEN", "TIP"), ("TIP", "CAI"), ("BEN", "CAI"), ("CAI", "HBE"), ("CAI", "ALY")):
            Route.objects.get_or_create(origin=origin, destination=destination, country=country, defaults={"is_active": True})

        employees = [
            self._ensure_record(
                Employee,
                {"badge_number": f"FD-EMP-{number:03d}"},
                {
                    "full_name": full_name,
                    "project": projects[number % len(projects)],
                    "department": departments[number % len(departments)],
                    "job_title": title,
                    "email": f"{full_name.split()[0].lower()}.demo@demo.local",
                    "is_active": True,
                },
            )
            for number, (full_name, title) in enumerate(
                [
                    ("Sami Demo", "Field Engineer"),
                    ("Nadia Demo", "Travel Coordinator"),
                    ("Khaled Demo", "Logistics Supervisor"),
                    ("Hana Demo", "Site Administrator"),
                    ("Tariq Demo", "Operations Lead"),
                ]
            )
        ]

        return {
            "country": country,
            "departments": departments,
            "projects": projects,
            "suppliers": suppliers,
            "employees": employees,
            "users": users,
        }

    def _ensure_record(self, model, lookup: dict, defaults: dict):
        try:
            with transaction.atomic():
                obj, _ = model.objects.get_or_create(**lookup, defaults=defaults)
        except IntegrityError:
            obj = model.objects.get(**lookup)
        changed_fields = []
        for field, value in defaults.items():
            if getattr(obj, field) != value:
                setattr(obj, field, value)
                changed_fields.append(field)
        if changed_fields:
            obj.save(update_fields=changed_fields)
        return obj

    def _group_user(self, user_model, username, group_name):
        user = user_model.objects.filter(username=username).first()
        if user:
            return user
        # Fallback so the command is resilient even if seed_demo_users changed.
        user, _ = user_model.objects.get_or_create(
            username=username,
            defaults={"email": f"{username}@demo.local", "first_name": group_name, "last_name": "Demo"},
        )
        group, _ = Group.objects.get_or_create(name=group_name)
        user.groups.add(group)
        return user

    # ----- chain building ----------------------------------------------

    def _build_chain(self, *, scenario, employee, project, department, supplier, country, users):
        key = scenario["key"]
        marker = f"{MARKER_PREFIX}:{key}"
        if TravelCase.objects.filter(notes__contains=marker).exists():
            return None

        hr_user = users["hr"]
        booking_user = users["booking"]
        finance_user = users["finance"]
        stage = scenario["stage"]
        route_from, route_to = scenario["route"]
        amount = scenario["amount"]
        currency = scenario["currency"]
        travel_date = scenario["travel_date"]

        with transaction.atomic():
            case = create_travel_case(
                {
                    "employee": employee,
                    "project": project,
                    "department": department,
                    "country": country,
                    "travel_purpose": TravelPurpose.BUSINESS_TRIP,
                    "account_type": AccountType.COMPANY,
                    "route_from": route_from,
                    "route_to": route_to,
                    "requested_travel_date": travel_date,
                    "requested_return_date": None,
                    "priority": Priority.NORMAL,
                    "approval_required": True,
                    "notes": f"Demo workflow chain for UAT. {marker}",
                },
                hr_user,
            )
            submit_travel_case(case, hr_user)
            if stage == STAGE_SUBMITTED:
                return case

            assign_travel_case(case, booking_user, booking_user)
            ticket_number = f"FD-{key.upper()}-0001"
            ticket_job = self._confirmed_ticket_job(employee, supplier, route_from, route_to, travel_date, amount, currency, ticket_number, booking_user)
            ticket = create_ticket_version_from_confirmed_extraction(ticket_job, case, TicketAction.ORIGINAL, user=booking_user)
            confirm_ticket_version(ticket, booking_user)
            if stage == STAGE_TICKETED:
                return case

            invoice_job = self._confirmed_invoice_job(key, supplier, ticket_number, route_from, route_to, travel_date, amount, currency, hr_user)
            invoice = create_supplier_invoice_from_confirmed_extraction(invoice_job, supplier, user=hr_user)
            if stage == STAGE_MATCHING:
                return case

            match_invoice(invoice, hr_user)
            approve_supplier_invoice(invoice, hr_user)
            if stage == STAGE_TBCN_READY:
                return case

            tbcn = generate_tbcn(invoice, hr_user)
            generate_tbcn_pdf(tbcn, hr_user)
            if stage == STAGE_TBCN_GENERATED:
                return case

            send_tbcn_to_finance(tbcn, finance_user)
            if stage == STAGE_FINANCE_SENT:
                return case

            mark_finance_accepted(tbcn, finance_user)
            mark_tbcn_paid(tbcn, finance_user)
            return case

    def _confirmed_ticket_job(self, employee, supplier, route_from, route_to, travel_date, amount, currency, ticket_number, user):
        return DocumentExtractionJob.objects.create(
            document_type=DocumentType.FLIGHT_TICKET,
            status=ExtractionStatus.CONFIRMED,
            normalized_data={
                "passenger_name": employee.full_name,
                "ticket_number": ticket_number,
                "pnr": f"FD{ticket_number[-4:]}",
                "airline": supplier.name,
                "route_from": route_from,
                "route_to": route_to,
                "departure_date": travel_date.isoformat(),
                "amount": amount,
                "currency": currency,
                "supplier": supplier.code,
                "ticket_status": "ACTIVE",
            },
            confidence_json={"passenger_name": 0.97, "ticket_number": 0.99, "amount": 0.95},
            confirmed_by=user,
            created_by=user,
        )

    def _confirmed_invoice_job(self, key, supplier, ticket_number, route_from, route_to, travel_date, amount, currency, user):
        return DocumentExtractionJob.objects.create(
            document_type=DocumentType.SUPPLIER_INVOICE,
            status=ExtractionStatus.CONFIRMED,
            normalized_data={
                "supplier_invoice_number": f"FD-INV-{key.upper()}-001",
                "invoice_date": travel_date.isoformat(),
                "received_date": travel_date.isoformat(),
                "currency": currency,
                "total_amount": amount,
                "country_code": "LY",
                "lines": [
                    {
                        "ticket_number": ticket_number,
                        "route_from": route_from,
                        "route_to": route_to,
                        "amount": amount,
                        "currency": currency,
                    }
                ],
            },
            confidence_json={"supplier_invoice_number": 0.98, "total_amount": 0.96},
            confirmed_by=user,
            created_by=user,
        )

    # ----- permits -----------------------------------------------------

    def _ensure_permits(self, user):
        scenarios = {
            STAGE_SUBMITTED: (PermitType.LIBYA_PERMIT, PermitStatus.PENDING, date(2026, 12, 31)),
            STAGE_TICKETED: (PermitType.EGYPT_PERMIT, PermitStatus.APPROVED, date(2026, 12, 31)),
            STAGE_PAID: (PermitType.LIBYA_PERMIT, PermitStatus.EXPIRED, date(2025, 1, 1)),
        }
        for key, (permit_type, status, expiry) in scenarios.items():
            case = TravelCase.objects.filter(notes__contains=f"{MARKER_PREFIX}:{key}").first()
            if not case:
                continue
            Permit.objects.get_or_create(
                travel_case=case,
                permit_type=permit_type,
                defaults={
                    "status": status,
                    "expiry_date": expiry,
                    "notes": "Demo permit for UAT walkthrough.",
                    "created_by": user,
                },
            )

    # ----- helpers -----------------------------------------------------

    def _reconcile_travel_case_sequence(self, code, year):
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
