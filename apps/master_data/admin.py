from django.contrib import admin

from .models import Country, Department, Employee, Project, Route, Supplier


@admin.register(Country)
class CountryAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "is_active")
    search_fields = ("code", "name")
    list_filter = ("is_active",)


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "country", "cost_center", "is_active")
    search_fields = ("code", "name", "cost_center")
    list_filter = ("country", "is_active")


@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "is_active")
    search_fields = ("code", "name")
    list_filter = ("is_active",)


@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "contact_name", "email", "phone", "is_active")
    search_fields = ("code", "name", "contact_name", "email", "tax_number")
    list_filter = ("is_active",)


@admin.register(Route)
class RouteAdmin(admin.ModelAdmin):
    list_display = ("origin", "destination", "country", "is_active")
    search_fields = ("origin", "destination")
    list_filter = ("country", "is_active")


@admin.register(Employee)
class EmployeeAdmin(admin.ModelAdmin):
    list_display = ("badge_number", "full_name", "project", "department", "job_title", "is_active")
    search_fields = ("badge_number", "full_name", "email", "phone")
    list_filter = ("project", "department", "is_active")

# Register your models here.
