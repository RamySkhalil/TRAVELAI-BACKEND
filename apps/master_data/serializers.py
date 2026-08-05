from rest_framework import serializers

from .models import Country, Department, Employee, Project, Route, Supplier


class CountrySerializer(serializers.ModelSerializer):
    class Meta:
        model = Country
        fields = "__all__"


class ProjectSerializer(serializers.ModelSerializer):
    class Meta:
        model = Project
        fields = "__all__"


class DepartmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Department
        fields = "__all__"


class SupplierSerializer(serializers.ModelSerializer):
    class Meta:
        model = Supplier
        fields = "__all__"


class RouteSerializer(serializers.ModelSerializer):
    class Meta:
        model = Route
        fields = "__all__"


class EmployeeSerializer(serializers.ModelSerializer):
    project_code = serializers.CharField(source="project.code", read_only=True)
    project_name = serializers.CharField(source="project.name", read_only=True)
    department_code = serializers.CharField(source="department.code", read_only=True)
    department_name = serializers.CharField(source="department.name", read_only=True)
    country = serializers.IntegerField(source="project.country_id", read_only=True)
    country_code = serializers.CharField(source="project.country.code", read_only=True)
    country_name = serializers.CharField(source="project.country.name", read_only=True)
    travel_case_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Employee
        fields = (
            "id",
            "created_at",
            "updated_at",
            "badge_number",
            "full_name",
            "project",
            "project_code",
            "project_name",
            "department",
            "department_code",
            "department_name",
            "country",
            "country_code",
            "country_name",
            "job_title",
            "email",
            "phone",
            "notes",
            "is_active",
            "travel_case_count",
        )

    def validate_badge_number(self, value):
        return value.strip()

    def validate_full_name(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Employee name is required.")
        return value
