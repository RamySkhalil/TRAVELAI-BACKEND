from rest_framework import serializers

from apps.common.currency import normalize_currency

from .models import TicketVersion


class TicketVersionSerializer(serializers.ModelSerializer):
    def validate_currency(self, value):
        return normalize_currency(value)

    class Meta:
        model = TicketVersion
        fields = "__all__"
        read_only_fields = ("version_number", "is_locked", "locked_at", "created_at", "updated_at")
