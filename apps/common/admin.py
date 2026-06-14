from django.contrib import admin

from .models import ControlSequence


@admin.register(ControlSequence)
class ControlSequenceAdmin(admin.ModelAdmin):
    list_display = ("code", "year", "last_number", "updated_at")
    search_fields = ("code",)
    list_filter = ("code", "year")
    readonly_fields = ("created_at", "updated_at")

# Register your models here.
