from django.shortcuts import get_object_or_404
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.common.permissions import RoleBasedOperationalPermission

from .filters import AiExtractionCorrectionFilter, DocumentExtractionJobFilter
from .models import AiExtractionCorrection, DocumentExtractionJob, DocumentType
from .serializers import AiExtractionCorrectionSerializer, DocumentExtractionJobSerializer
from .services import create_extraction_job, confirm_extraction_job, reject_extraction_job, run_invoice_extraction, run_ticket_extraction


class AiExtractionPermission(RoleBasedOperationalPermission):
    write_groups = ("Admin", "HR", "BookingOfficer", "BookingManager", "Finance")


class DocumentExtractionJobViewSet(viewsets.ModelViewSet):
    queryset = DocumentExtractionJob.objects.select_related("created_by", "confirmed_by", "rejected_by").all()
    serializer_class = DocumentExtractionJobSerializer
    permission_classes = [AiExtractionPermission]
    filterset_class = DocumentExtractionJobFilter
    search_fields = ["document_type", "raw_extracted_data", "normalized_data"]
    ordering_fields = ["created_at", "status", "document_type"]

    @action(detail=False, methods=["post"], url_path="extract-ticket")
    def extract_ticket(self, request):
        job = self._get_or_create_job(request, DocumentType.FLIGHT_TICKET)
        job = run_ticket_extraction(job, request.data.get("provider", "mock"), request.user)
        return Response(self.get_serializer(job).data)

    @action(detail=False, methods=["post"], url_path="extract-invoice")
    def extract_invoice(self, request):
        job = self._get_or_create_job(request, DocumentType.SUPPLIER_INVOICE)
        job = run_invoice_extraction(job, request.data.get("provider", "mock"), request.user)
        return Response(self.get_serializer(job).data)

    @action(detail=True, methods=["post"])
    def confirm(self, request, pk=None):
        corrected_data = request.data.get("corrected_data", request.data.get("normalized_data"))
        job = confirm_extraction_job(self.get_object(), corrected_data, request.user)
        return Response(self.get_serializer(job).data)

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        job = reject_extraction_job(self.get_object(), request.data.get("reason", ""), request.user)
        return Response(self.get_serializer(job).data)

    def _get_or_create_job(self, request, document_type):
        job_id = request.data.get("extraction_job")
        if job_id:
            return get_object_or_404(DocumentExtractionJob, pk=job_id)
        return create_extraction_job(
            document_type=document_type,
            source_file=request.FILES.get("source_file"),
            raw_text=request.data.get("raw_text", ""),
            user=request.user,
        )


class AiExtractionCorrectionViewSet(viewsets.ModelViewSet):
    queryset = AiExtractionCorrection.objects.select_related("extraction_job", "corrected_by").all()
    serializer_class = AiExtractionCorrectionSerializer
    permission_classes = [AiExtractionPermission]
    filterset_class = AiExtractionCorrectionFilter
    search_fields = ["field_name", "correction_reason"]
    ordering_fields = ["corrected_at", "field_name"]

# Create your views here.
