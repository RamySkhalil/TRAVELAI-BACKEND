from django.contrib import admin

from .models import Permit


@admin.register(Permit)
class PermitAdmin(admin.ModelAdmin):
    list_display = ("travel_case", "permit_type", "status", "issue_date", "expiry_date", "created_by")
    search_fields = ("travel_case__case_number", "notes")
    list_filter = ("permit_type", "status", "expiry_date")
    readonly_fields = ("created_at", "updated_at")

# Register your models here.
