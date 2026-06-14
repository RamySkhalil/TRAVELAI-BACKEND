from django.contrib import admin

from .models import SupplierInvoice, SupplierInvoiceLine


class SupplierInvoiceLineInline(admin.TabularInline):
    model = SupplierInvoiceLine
    extra = 0
    readonly_fields = ("created_at", "updated_at")


@admin.register(SupplierInvoice)
class SupplierInvoiceAdmin(admin.ModelAdmin):
    list_display = ("invoice_record_number", "supplier", "supplier_invoice_number", "invoice_date", "received_date", "total_amount", "currency", "status", "is_locked")
    search_fields = ("invoice_record_number", "supplier_invoice_number", "supplier__name")
    list_filter = ("supplier", "status", "currency", "is_locked", "invoice_date", "received_date")
    readonly_fields = ("uid", "created_at", "updated_at", "locked_at")
    inlines = (SupplierInvoiceLineInline,)


@admin.register(SupplierInvoiceLine)
class SupplierInvoiceLineAdmin(admin.ModelAdmin):
    list_display = ("supplier_invoice", "ticket_number", "travel_case", "ticket_version", "booked_amount", "invoiced_amount", "difference_amount", "match_status", "is_locked")
    search_fields = ("supplier_invoice__invoice_record_number", "ticket_number", "travel_case__case_number", "employee__full_name")
    list_filter = ("match_status", "account_type", "is_locked")
    readonly_fields = ("created_at", "updated_at")

# Register your models here.
