import csv

from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404
from django.utils.dateparse import parse_date
from drf_spectacular.utils import OpenApiTypes, extend_schema
from rest_framework import status, viewsets
from rest_framework.views import APIView
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.common.permissions import RoleBasedOperationalPermission

from .filters import TravelBillingConfirmationNoteFilter, TravelBillingConfirmationRevisionFilter
from .models import TravelBillingConfirmationNote, TravelBillingConfirmationRevision
from .serializers import TravelBillingConfirmationNoteSerializer, TravelBillingConfirmationRevisionSerializer
from .services import generate_tbcn, generate_tbcn_pdf, mark_finance_accepted, mark_tbcn_paid, send_tbcn_to_finance
from apps.supplier_invoices.models import SupplierInvoice


class BillingConfirmationPermission(RoleBasedOperationalPermission):
    write_groups = ("Admin", "Finance", "BookingManager")
    pdf_groups = ("Admin", "Finance", "HR")

    def has_permission(self, request, view):
        if getattr(view, "action", "") == "generate_pdf":
            return _user_in_groups(request.user, self.pdf_groups)
        return super().has_permission(request, view)


class FinanceControlReportPermission(RoleBasedOperationalPermission):
    write_groups = ()

    def has_permission(self, request, view):
        return request.method == "GET" and _user_in_groups(request.user, ("Admin", "Finance", "HR", "Auditor"))


class TravelBillingConfirmationNoteViewSet(viewsets.ModelViewSet):
    queryset = (
        TravelBillingConfirmationNote.objects.select_related(
            "generated_by",
            "reviewed_by",
            "sent_to_finance_by",
            "supplier_invoice",
            "supplier",
        )
        .prefetch_related("supplier_invoice__lines")
        .all()
    )
    serializer_class = TravelBillingConfirmationNoteSerializer
    permission_classes = [BillingConfirmationPermission]
    filterset_class = TravelBillingConfirmationNoteFilter
    search_fields = ["confirmation_no", "supplier_invoice_number", "supplier__name"]
    ordering_fields = ["generated_at", "status", "finance_status", "confirmation_no"]

    @action(detail=False, methods=["post"], url_path="generate-tbcn")
    def generate_tbcn(self, request, pk=None):
        invoice = get_object_or_404(SupplierInvoice, pk=request.data.get("supplier_invoice"))
        tbcn = generate_tbcn(invoice, request.user)
        return Response(self.get_serializer(tbcn).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path="send-to-finance")
    def send_to_finance(self, request, pk=None):
        tbcn = send_tbcn_to_finance(self.get_object(), request.user)
        return Response(self.get_serializer(tbcn).data)

    @action(detail=True, methods=["post"], url_path="mark-finance-accepted")
    def mark_finance_accepted(self, request, pk=None):
        tbcn = mark_finance_accepted(self.get_object(), request.user)
        return Response(self.get_serializer(tbcn).data)

    @action(detail=True, methods=["post"], url_path="mark-paid")
    def mark_paid(self, request, pk=None):
        tbcn = mark_tbcn_paid(self.get_object(), request.user)
        return Response(self.get_serializer(tbcn).data)

    @action(detail=True, methods=["post"], url_path="generate-pdf")
    def generate_pdf(self, request, pk=None):
        tbcn = generate_tbcn_pdf(self.get_object(), request.user)
        return Response(self.get_serializer(tbcn).data)

    @action(detail=True, methods=["get"], url_path="download-pdf")
    def download_pdf(self, request, pk=None):
        tbcn = self.get_object()
        if not _user_in_groups(request.user, ("Admin", "Finance", "HR")):
            return Response({"detail": "You do not have permission to download TBCN PDFs."}, status=status.HTTP_403_FORBIDDEN)
        if not tbcn.pdf_file:
            raise Http404("TBCN PDF has not been generated.")
        return FileResponse(
            tbcn.pdf_file.open("rb"),
            content_type="application/pdf",
            as_attachment=False,
            filename=f"{tbcn.confirmation_no}.pdf",
        )

    @action(detail=True, methods=["post"], url_path="create-revision")
    def create_revision(self, request, pk=None):
        return Response({"detail": "TBCN revision workflow is reserved for Phase 4 implementation."}, status=status.HTTP_501_NOT_IMPLEMENTED)


class TravelBillingConfirmationRevisionViewSet(viewsets.ModelViewSet):
    queryset = TravelBillingConfirmationRevision.objects.select_related("original_tbcn", "revised_by").all()
    serializer_class = TravelBillingConfirmationRevisionSerializer
    permission_classes = [BillingConfirmationPermission]
    filterset_class = TravelBillingConfirmationRevisionFilter
    search_fields = ["original_tbcn__confirmation_no", "reason"]
    ordering_fields = ["revised_at", "revision_number"]


class FinanceControlReportView(APIView):
    permission_classes = [FinanceControlReportPermission]
    serializer_class = TravelBillingConfirmationNoteSerializer

    @extend_schema(responses=TravelBillingConfirmationNoteSerializer(many=True))
    def get(self, request):
        queryset = filter_finance_control_report_queryset(request.query_params)
        serializer = TravelBillingConfirmationNoteSerializer(queryset, many=True, context={"request": request})
        return Response(serializer.data)


class FinanceControlReportCsvExportView(APIView):
    permission_classes = [FinanceControlReportPermission]
    serializer_class = TravelBillingConfirmationNoteSerializer

    @extend_schema(responses={(200, "text/csv"): OpenApiTypes.BINARY})
    def get(self, request):
        queryset = filter_finance_control_report_queryset(request.query_params)
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="finance-control-report.csv"'
        writer = csv.writer(response)
        writer.writerow(
            [
                "TBCN number",
                "Supplier",
                "Supplier invoice number",
                "Total amount",
                "Matched amount",
                "Difference amount",
                "Currency",
                "Status",
                "Finance status",
                "Generated at",
                "Sent to finance at",
                "Paid status/date",
                "PDF available",
            ]
        )
        for tbcn in queryset:
            writer.writerow(
                [
                    tbcn.confirmation_no,
                    tbcn.supplier.name,
                    tbcn.supplier_invoice_number,
                    tbcn.total_amount,
                    tbcn.matched_amount,
                    tbcn.difference_amount,
                    tbcn.currency,
                    tbcn.status,
                    tbcn.finance_status,
                    tbcn.generated_at.isoformat() if tbcn.generated_at else "",
                    tbcn.sent_to_finance_at.isoformat() if tbcn.sent_to_finance_at else "",
                    tbcn.updated_at.isoformat() if tbcn.finance_status == "PAID" else "Unpaid",
                    "Yes" if tbcn.pdf_file else "No",
                ]
            )
        return response


def filter_finance_control_report_queryset(params):
    queryset = TravelBillingConfirmationNote.objects.select_related("supplier", "supplier_invoice", "generated_by").order_by("-generated_at")
    if supplier := params.get("supplier"):
        queryset = queryset.filter(supplier=supplier)
    if status_value := params.get("status"):
        queryset = queryset.filter(status=status_value)
    if finance_status := params.get("finance_status"):
        queryset = queryset.filter(finance_status=finance_status)
    if currency := params.get("currency"):
        queryset = queryset.filter(currency=currency.upper())
    if generated_from := parse_date(params.get("generated_from") or params.get("date_from") or ""):
        queryset = queryset.filter(generated_at__date__gte=generated_from)
    if generated_to := parse_date(params.get("generated_to") or params.get("date_to") or ""):
        queryset = queryset.filter(generated_at__date__lte=generated_to)
    paid = params.get("paid")
    if paid in ("true", "1", "yes"):
        queryset = queryset.filter(finance_status="PAID")
    elif paid in ("false", "0", "no"):
        queryset = queryset.exclude(finance_status="PAID")
    return queryset


def _user_in_groups(user, group_names):
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser or user.is_staff:
        return True
    return user.groups.filter(name__in=group_names).exists()

# Create your views here.
