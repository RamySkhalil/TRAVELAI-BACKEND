from django.contrib import admin

from .models import TravelBillingConfirmationNote, TravelBillingConfirmationRevision


class TravelBillingConfirmationRevisionInline(admin.TabularInline):
    model = TravelBillingConfirmationRevision
    extra = 0
    readonly_fields = ("created_at", "revised_at")


@admin.register(TravelBillingConfirmationNote)
class TravelBillingConfirmationNoteAdmin(admin.ModelAdmin):
    list_display = ("confirmation_no", "supplier_invoice", "supplier", "total_amount", "matched_amount", "difference_amount", "currency", "status", "finance_status", "is_locked")
    search_fields = ("confirmation_no", "supplier_invoice_number", "supplier__name", "supplier_invoice__invoice_record_number")
    list_filter = ("status", "finance_status", "supplier", "currency", "is_locked")
    readonly_fields = ("uid", "created_at", "updated_at", "generated_at", "reviewed_at", "sent_to_finance_at")
    inlines = (TravelBillingConfirmationRevisionInline,)


@admin.register(TravelBillingConfirmationRevision)
class TravelBillingConfirmationRevisionAdmin(admin.ModelAdmin):
    list_display = ("original_tbcn", "revision_number", "revised_by", "revised_at")
    search_fields = ("original_tbcn__confirmation_no", "reason")
    list_filter = ("revised_at",)
    readonly_fields = ("created_at",)

# Register your models here.
