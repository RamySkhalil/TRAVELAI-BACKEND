from django.db.models import Count
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.audit_logs.services import create_audit_log
from apps.common.permissions import IsAdminOrReadOnly

from .filters import CountryFilter, DepartmentFilter, EmployeeFilter, ProjectFilter, RouteFilter, SupplierFilter
from .models import Country, Department, Employee, Project, Route, Supplier
from .serializers import CountrySerializer, DepartmentSerializer, EmployeeSerializer, ProjectSerializer, RouteSerializer, SupplierSerializer


class CountryViewSet(viewsets.ModelViewSet):
    queryset = Country.objects.all()
    serializer_class = CountrySerializer
    permission_classes = [IsAdminOrReadOnly]
    filterset_class = CountryFilter
    search_fields = ["code", "name"]
    ordering_fields = ["code", "name", "created_at"]


class ProjectViewSet(viewsets.ModelViewSet):
    queryset = Project.objects.select_related("country").all()
    serializer_class = ProjectSerializer
    permission_classes = [IsAdminOrReadOnly]
    filterset_class = ProjectFilter
    search_fields = ["code", "name", "cost_center"]
    ordering_fields = ["code", "name", "created_at"]


class DepartmentViewSet(viewsets.ModelViewSet):
    queryset = Department.objects.all()
    serializer_class = DepartmentSerializer
    permission_classes = [IsAdminOrReadOnly]
    filterset_class = DepartmentFilter
    search_fields = ["code", "name"]
    ordering_fields = ["code", "name", "created_at"]


class SupplierViewSet(viewsets.ModelViewSet):
    queryset = Supplier.objects.all()
    serializer_class = SupplierSerializer
    permission_classes = [IsAdminOrReadOnly]
    filterset_class = SupplierFilter
    search_fields = ["code", "name", "contact_name", "email", "tax_number"]
    ordering_fields = ["code", "name", "created_at"]


class RouteViewSet(viewsets.ModelViewSet):
    queryset = Route.objects.select_related("country").all()
    serializer_class = RouteSerializer
    permission_classes = [IsAdminOrReadOnly]
    filterset_class = RouteFilter
    search_fields = ["origin", "destination"]
    ordering_fields = ["origin", "destination", "created_at"]


class EmployeeViewSet(viewsets.ModelViewSet):
    queryset = Employee.objects.select_related("project", "project__country", "department").annotate(travel_case_count=Count("travel_cases"))
    serializer_class = EmployeeSerializer
    permission_classes = [IsAdminOrReadOnly]
    filterset_class = EmployeeFilter
    search_fields = ["badge_number", "full_name", "email", "phone"]
    ordering_fields = ["badge_number", "full_name", "created_at", "updated_at"]
    http_method_names = ["get", "post", "patch", "head", "options"]

    def perform_create(self, serializer):
        employee = serializer.save()
        create_audit_log(
            user=self.request.user,
            action="Employee Created",
            entity_type="Employee",
            entity_id=employee.id,
            new_value=_employee_audit_value(employee),
        )

    def perform_update(self, serializer):
        old_value = _employee_audit_value(self.get_object())
        employee = serializer.save()
        create_audit_log(
            user=self.request.user,
            action="Employee Updated",
            entity_type="Employee",
            entity_id=employee.id,
            old_value=old_value,
            new_value=_employee_audit_value(employee),
        )

    @action(detail=True, methods=["post"])
    def activate(self, request, pk=None):
        employee = self.get_object()
        old_value = _employee_audit_value(employee)
        if not employee.is_active:
            employee.is_active = True
            employee.save(update_fields=["is_active", "updated_at"])
        create_audit_log(
            user=request.user,
            action="Employee Activated",
            entity_type="Employee",
            entity_id=employee.id,
            old_value=old_value,
            new_value=_employee_audit_value(employee),
        )
        return Response(self.get_serializer(employee).data)

    @action(detail=True, methods=["post"])
    def deactivate(self, request, pk=None):
        employee = self.get_object()
        old_value = _employee_audit_value(employee)
        if employee.is_active:
            employee.is_active = False
            employee.save(update_fields=["is_active", "updated_at"])
        create_audit_log(
            user=request.user,
            action="Employee Deactivated",
            entity_type="Employee",
            entity_id=employee.id,
            old_value=old_value,
            new_value=_employee_audit_value(employee),
        )
        return Response(self.get_serializer(employee).data)


def _employee_audit_value(employee: Employee) -> dict:
    return {
        "badge_number": employee.badge_number,
        "full_name": employee.full_name,
        "project": employee.project_id,
        "department": employee.department_id,
        "job_title": employee.job_title,
        "email": employee.email,
        "phone": employee.phone,
        "notes": employee.notes,
        "is_active": employee.is_active,
    }

# Create your views here.
