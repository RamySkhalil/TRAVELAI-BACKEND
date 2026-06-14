from django.contrib import admin

from .models import AiExtractionCorrection, DocumentExtractionJob


class AiExtractionCorrectionInline(admin.TabularInline):
    model = AiExtractionCorrection
    extra = 0
    readonly_fields = ("corrected_at",)


@admin.register(DocumentExtractionJob)
class DocumentExtractionJobAdmin(admin.ModelAdmin):
    list_display = ("uid", "document_type", "status", "created_by", "confirmed_by", "confirmed_at", "rejected_by", "rejected_at", "created_at")
    search_fields = ("uid",)
    list_filter = ("document_type", "status", "created_at")
    readonly_fields = ("uid", "created_at", "updated_at", "confirmed_at", "rejected_at")
    inlines = (AiExtractionCorrectionInline,)


@admin.register(AiExtractionCorrection)
class AiExtractionCorrectionAdmin(admin.ModelAdmin):
    list_display = ("extraction_job", "field_name", "corrected_by", "corrected_at")
    search_fields = ("field_name", "correction_reason")
    list_filter = ("corrected_at", "corrected_by")
    readonly_fields = ("corrected_at",)

# Register your models here.
