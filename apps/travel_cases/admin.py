from django.contrib import admin

from .models import TravelCase


@admin.register(TravelCase)
class TravelCaseAdmin(admin.ModelAdmin):
    list_display = ("case_number", "employee_name", "project", "country", "account_type", "current_status", "priority", "requested_travel_date")
    search_fields = ("case_number", "employee_name", "badge_number", "route_from", "route_to")
    list_filter = ("current_status", "account_type", "priority", "project", "country")
    readonly_fields = ("uid", "created_at", "updated_at", "submitted_at", "closed_at")

# Register your models here.
