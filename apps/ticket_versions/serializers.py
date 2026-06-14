from rest_framework import serializers

from .models import TicketVersion


class TicketVersionSerializer(serializers.ModelSerializer):
    class Meta:
        model = TicketVersion
        fields = "__all__"
        read_only_fields = ("version_number", "is_locked", "locked_at", "created_at", "updated_at")
