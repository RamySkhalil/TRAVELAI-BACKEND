from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import AccessScopeViewSet, UserManagementViewSet, available_roles

router = DefaultRouter()
router.register("users", UserManagementViewSet, basename="access-control-user")
router.register("scopes", AccessScopeViewSet, basename="access-control-scope")

urlpatterns = [
    path("roles/", available_roles, name="access-control-roles"),
    path("", include(router.urls)),
]
