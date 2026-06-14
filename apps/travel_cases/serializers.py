from rest_framework import serializers

from .models import TravelCase


class TravelCaseSerializer(serializers.ModelSerializer):
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
            "submitted_at",
            "closed_at",
            "created_at",
            "updated_at",
        )
