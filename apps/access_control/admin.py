from django.contrib import admin

from .models import TravelOpsUserProfile, UserAccessScope


@admin.register(TravelOpsUserProfile)
class TravelOpsUserProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "display_name", "job_title", "is_travelops_active", "is_super_admin")
    list_filter = ("is_travelops_active", "is_super_admin")
    search_fields = ("user__username", "display_name", "job_title")
    raw_id_fields = ("user",)


@admin.register(UserAccessScope)
class UserAccessScopeAdmin(admin.ModelAdmin):
    list_display = ("user", "scope_type", "country", "project", "department", "is_active")
    list_filter = ("scope_type", "is_active")
    search_fields = ("user__username",)
    raw_id_fields = ("user", "country", "project", "department")
