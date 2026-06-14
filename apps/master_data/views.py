from rest_framework import viewsets

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
    queryset = Employee.objects.select_related("project", "department").all()
    serializer_class = EmployeeSerializer
    permission_classes = [IsAdminOrReadOnly]
    filterset_class = EmployeeFilter
    search_fields = ["badge_number", "full_name", "email", "phone"]
    ordering_fields = ["badge_number", "full_name", "created_at"]

# Create your views here.
