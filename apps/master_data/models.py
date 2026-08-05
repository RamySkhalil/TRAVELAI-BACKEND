from django.db import models

from apps.common.models import TimeStampedModel


class Country(TimeStampedModel):
    code = models.CharField(max_length=10, unique=True)
    name = models.CharField(max_length=120)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name_plural = "countries"
        indexes = [
            models.Index(fields=["code"]),
            models.Index(fields=["name"]),
            models.Index(fields=["is_active"]),
        ]
        ordering = ["code"]

    def __str__(self) -> str:
        return f"{self.code} - {self.name}"


class Project(TimeStampedModel):
    code = models.CharField(max_length=40, unique=True)
    name = models.CharField(max_length=160)
    country = models.ForeignKey(Country, on_delete=models.PROTECT, related_name="projects")
    cost_center = models.CharField(max_length=80, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        indexes = [
            models.Index(fields=["code"]),
            models.Index(fields=["name"]),
            models.Index(fields=["country", "is_active"]),
        ]
        ordering = ["code"]

    def __str__(self) -> str:
        return f"{self.code} - {self.name}"


class Department(TimeStampedModel):
    code = models.CharField(max_length=40, unique=True)
    name = models.CharField(max_length=160)
    is_active = models.BooleanField(default=True)

    class Meta:
        indexes = [
            models.Index(fields=["code"]),
            models.Index(fields=["name"]),
            models.Index(fields=["is_active"]),
        ]
        ordering = ["code"]

    def __str__(self) -> str:
        return f"{self.code} - {self.name}"


class Supplier(TimeStampedModel):
    code = models.CharField(max_length=40, unique=True)
    name = models.CharField(max_length=180)
    contact_name = models.CharField(max_length=120, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=40, blank=True)
    tax_number = models.CharField(max_length=80, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        indexes = [
            models.Index(fields=["code"]),
            models.Index(fields=["name"]),
            models.Index(fields=["is_active"]),
        ]
        ordering = ["name"]

    def __str__(self) -> str:
        return f"{self.code} - {self.name}"


class Route(TimeStampedModel):
    origin = models.CharField(max_length=80)
    destination = models.CharField(max_length=80)
    country = models.ForeignKey(Country, on_delete=models.PROTECT, related_name="routes")
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["origin", "destination", "country"], name="unique_route_per_country"),
        ]
        indexes = [
            models.Index(fields=["origin"]),
            models.Index(fields=["destination"]),
            models.Index(fields=["country", "is_active"]),
        ]
        ordering = ["origin", "destination"]

    def __str__(self) -> str:
        return f"{self.origin} -> {self.destination}"


class Employee(TimeStampedModel):
    badge_number = models.CharField(max_length=40, unique=True)
    full_name = models.CharField(max_length=180)
    project = models.ForeignKey(Project, on_delete=models.PROTECT, related_name="employees")
    department = models.ForeignKey(Department, on_delete=models.PROTECT, related_name="employees")
    job_title = models.CharField(max_length=120, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=40, blank=True)
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        indexes = [
            models.Index(fields=["badge_number"]),
            models.Index(fields=["full_name"]),
            models.Index(fields=["project", "is_active"]),
            models.Index(fields=["department", "is_active"]),
        ]
        ordering = ["full_name"]

    def __str__(self) -> str:
        return f"{self.badge_number} - {self.full_name}"

# Create your models here.
