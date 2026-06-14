"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from apps.ai_extraction.views import AiExtractionCorrectionViewSet, DocumentExtractionJobViewSet
from apps.audit_logs.views import AuditLogViewSet
from apps.billing_confirmations.views import TravelBillingConfirmationNoteViewSet, TravelBillingConfirmationRevisionViewSet
from apps.dashboard.views import DashboardViewSet
from apps.invoice_matching.views import InvoiceMatchingViewSet
from apps.master_data.views import CountryViewSet, DepartmentViewSet, EmployeeViewSet, ProjectViewSet, RouteViewSet, SupplierViewSet
from apps.permits.views import PermitViewSet
from apps.supplier_invoices.views import SupplierInvoiceLineViewSet, SupplierInvoiceViewSet
from apps.ticket_versions.views import TicketVersionViewSet
from apps.travel_cases.views import TravelCaseViewSet

router = DefaultRouter()
router.register("countries", CountryViewSet, basename="country")
router.register("projects", ProjectViewSet, basename="project")
router.register("departments", DepartmentViewSet, basename="department")
router.register("suppliers", SupplierViewSet, basename="supplier")
router.register("routes", RouteViewSet, basename="route")
router.register("employees", EmployeeViewSet, basename="employee")
router.register("travel-cases", TravelCaseViewSet, basename="travel-case")
router.register("ticket-versions", TicketVersionViewSet, basename="ticket-version")
router.register("permits", PermitViewSet, basename="permit")
router.register("supplier-invoices", SupplierInvoiceViewSet, basename="supplier-invoice")
router.register("supplier-invoice-lines", SupplierInvoiceLineViewSet, basename="supplier-invoice-line")
router.register("invoice-matching", InvoiceMatchingViewSet, basename="invoice-matching")
router.register("tbcn", TravelBillingConfirmationNoteViewSet, basename="tbcn")
router.register("tbcn-revisions", TravelBillingConfirmationRevisionViewSet, basename="tbcn-revision")
router.register("ai-extraction-jobs", DocumentExtractionJobViewSet, basename="ai-extraction-job")
router.register("ai-extraction-corrections", AiExtractionCorrectionViewSet, basename="ai-extraction-correction")
router.register("audit-logs", AuditLogViewSet, basename="audit-log")
router.register("dashboard", DashboardViewSet, basename="dashboard")

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/schema/', SpectacularAPIView.as_view(), name='schema'),
    path('api/docs/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
    path('api/v1/auth/login/', TokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('api/v1/auth/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    path('api/v1/', include(router.urls)),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
