import django_filters

from .models import Country, Department, Employee, Project, Route, Supplier


class ActiveFilterSet(django_filters.FilterSet):
    is_active = django_filters.BooleanFilter()


class CountryFilter(ActiveFilterSet):
    class Meta:
        model = Country
        fields = ["is_active"]


class ProjectFilter(ActiveFilterSet):
    class Meta:
        model = Project
        fields = ["country", "is_active"]


class DepartmentFilter(ActiveFilterSet):
    class Meta:
        model = Department
        fields = ["is_active"]


class SupplierFilter(ActiveFilterSet):
    class Meta:
        model = Supplier
        fields = ["is_active"]


class RouteFilter(ActiveFilterSet):
    class Meta:
        model = Route
        fields = ["country", "is_active"]


class EmployeeFilter(ActiveFilterSet):
    class Meta:
        model = Employee
        fields = ["project", "department", "is_active"]
