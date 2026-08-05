from rest_framework import serializers

from .models import TravelCase


class TravelCaseSerializer(serializers.ModelSerializer):
    created_by_username = serializers.CharField(source="created_by.username", read_only=True)
    assigned_to_username = serializers.CharField(source="assigned_to.username", read_only=True)

    class Meta:
        model = TravelCase
        fields = "__all__"
        read_only_fields = (
            "uid",
            "case_number",
            "badge_number",
            "employee_name",
            "current_status",
            "created_by",
            "created_by_username",
            "assigned_to_username",
            "submitted_at",
            "closed_at",
            "created_at",
            "updated_at",
        )
