from django.contrib import admin

from .models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "user", "action", "entity_type", "entity_id", "ip_address")
    search_fields = ("action", "entity_type", "entity_id", "user__username")
    list_filter = ("action", "entity_type", "created_at")
    readonly_fields = ("created_at",)

# Register your models here.
