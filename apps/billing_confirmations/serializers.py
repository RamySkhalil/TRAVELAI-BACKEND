from rest_framework import serializers

from .models import TravelBillingConfirmationNote, TravelBillingConfirmationRevision


class TravelBillingConfirmationNoteSerializer(serializers.ModelSerializer):
    class Meta:
        model = TravelBillingConfirmationNote
        fields = "__all__"
        read_only_fields = ("uid", "confirmation_no", "is_locked", "created_at", "updated_at")


class TravelBillingConfirmationRevisionSerializer(serializers.ModelSerializer):
    class Meta:
        model = TravelBillingConfirmationRevision
        fields = "__all__"
        read_only_fields = ("created_at",)
