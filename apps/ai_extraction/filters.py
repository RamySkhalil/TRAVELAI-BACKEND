import django_filters

from .models import AiExtractionCorrection, DocumentExtractionJob


class DocumentExtractionJobFilter(django_filters.FilterSet):
    class Meta:
        model = DocumentExtractionJob
        fields = [
            "document_type",
            "status",
            "created_by",
            "confirmed_by",
        ]


class AiExtractionCorrectionFilter(django_filters.FilterSet):
    class Meta:
        model = AiExtractionCorrection
        fields = ["extraction_job", "field_name", "corrected_by", "corrected_at"]
