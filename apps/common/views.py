from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response


class CurrentUserSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    username = serializers.CharField()
    email = serializers.EmailField(allow_blank=True)
    first_name = serializers.CharField(allow_blank=True)
    last_name = serializers.CharField(allow_blank=True)
    is_staff = serializers.BooleanField()
    is_superuser = serializers.BooleanField()
    groups = serializers.ListField(child=serializers.CharField())
    permissions = serializers.ListField(child=serializers.CharField())


@extend_schema(responses=CurrentUserSerializer)
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def current_user(request):
    user = request.user

    return Response(
        {
            "id": user.id,
            "username": user.get_username(),
            "email": user.email,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "is_staff": user.is_staff,
            "is_superuser": user.is_superuser,
            "groups": list(user.groups.order_by("name").values_list("name", flat=True)),
            "permissions": sorted(user.get_all_permissions()),
        }
    )
