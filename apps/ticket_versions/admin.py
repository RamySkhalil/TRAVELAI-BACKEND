from django.contrib import admin

from .models import TicketVersion


@admin.register(TicketVersion)
class TicketVersionAdmin(admin.ModelAdmin):
    list_display = ("travel_case", "version_number", "ticket_action", "ticket_number", "pnr", "supplier", "ticket_status", "amount", "currency", "is_locked")
    search_fields = ("travel_case__case_number", "ticket_number", "pnr", "passenger_name", "airline")
    list_filter = ("ticket_action", "ticket_status", "supplier", "is_locked", "departure_date")
    readonly_fields = ("created_at", "updated_at", "locked_at")

# Register your models here.
