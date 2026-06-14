from rest_framework import serializers

from .models import AiExtractionCorrection, DocumentExtractionJob


class DocumentExtractionJobSerializer(serializers.ModelSerializer):
    class Meta:
        model = DocumentExtractionJob
        fields = "__all__"
        read_only_fields = ("uid", "created_at", "updated_at")


class AiExtractionCorrectionSerializer(serializers.ModelSerializer):
    class Meta:
        model = AiExtractionCorrection
        fields = "__all__"
        read_only_fields = ("corrected_at",)
