from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction


GROUP_NAMES = ("SuperAdmin", "Admin", "HR", "BookingOfficer", "BookingManager", "Finance", "Auditor")
DEMO_PASSWORD = "TravelOpsDemo123!"

DEMO_USERS = (
    {"username": "superadmin_demo", "groups": ("SuperAdmin",), "is_staff": True, "is_superuser": True, "first_name": "Super Admin", "is_super_admin": True},
    {"username": "admin_demo", "groups": ("Admin",), "is_staff": True, "first_name": "Admin"},
    {"username": "hr_demo", "groups": ("HR",), "first_name": "HR"},
    {"username": "booking_demo", "groups": ("BookingOfficer",), "first_name": "Booking"},
    {"username": "booking_manager_demo", "groups": ("BookingManager",), "first_name": "Booking Manager"},
    {"username": "finance_demo", "groups": ("Finance",), "first_name": "Finance"},
    {"username": "auditor_demo", "groups": ("Auditor",), "first_name": "Auditor"},
)


class Command(BaseCommand):
    help = "Seed local development demo users and operational groups."

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError("Refusing to seed demo users when DJANGO_DEBUG is not True.")

        user_model = get_user_model()

        from apps.access_control.models import TravelOpsUserProfile

        with transaction.atomic():
            Group.objects.bulk_create([Group(name=name) for name in GROUP_NAMES], ignore_conflicts=True)
            groups = {group.name: group for group in Group.objects.filter(name__in=GROUP_NAMES)}

            for user_config in DEMO_USERS:
                username = user_config["username"]
                user, created = user_model.objects.get_or_create(
                    username=username,
                    defaults={
                        "email": f"{username}@demo.local",
                        "first_name": user_config.get("first_name", ""),
                        "last_name": "Demo",
                        "is_staff": user_config.get("is_staff", False),
                        "is_superuser": user_config.get("is_superuser", False),
                    },
                )
                if created:
                    user.set_password(DEMO_PASSWORD)
                    user.save(update_fields=["password"])
                else:
                    update_fields = []
                    for field in ("is_staff", "is_superuser"):
                        next_value = user_config.get(field, False)
                        if getattr(user, field) != next_value:
                            setattr(user, field, next_value)
                            update_fields.append(field)
                    if update_fields:
                        user.save(update_fields=update_fields)

                user.groups.set(groups[name] for name in user_config["groups"])

                TravelOpsUserProfile.objects.update_or_create(
                    user=user,
                    defaults={
                        "display_name": f"{user_config.get('first_name', username)} Demo".strip(),
                        "is_super_admin": user_config.get("is_super_admin", False),
                        "is_travelops_active": True,
                    },
                )

        self.stdout.write(self.style.WARNING("DEVELOPMENT ONLY: demo users use a shared local password."))
        self.stdout.write(self.style.WARNING("Do not use these credentials outside local development."))
        self.stdout.write("")
        self.stdout.write(f"Password for all demo users: {DEMO_PASSWORD}")
        for user_config in DEMO_USERS:
            self.stdout.write(f"- {user_config['username']}: {', '.join(user_config['groups'])}")
