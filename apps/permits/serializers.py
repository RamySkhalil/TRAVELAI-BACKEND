from rest_framework import serializers

from .models import Permit


class PermitSerializer(serializers.ModelSerializer):
    class Meta:
        model = Permit
        fields = "__all__"
        read_only_fields = ("created_at", "updated_at")
