from drf_spectacular.utils import extend_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .serializers import CopilotChatRequestSerializer, CopilotChatResponseSerializer
from .services import run_copilot_chat


class CopilotChatView(APIView):
    """Read-only TravelOps Copilot chat endpoint.

    Answers questions using approved read-only tools only. It never mutates
    data, never runs free-form SQL, and always respects the existing backend
    permission boundary (authenticated users only; role-aware where relevant).
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(request=CopilotChatRequestSerializer, responses=CopilotChatResponseSerializer)
    def post(self, request):
        serializer = CopilotChatRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        context = data.get("context") or {}
        answer = run_copilot_chat(request.user, data["message"], context)
        return Response(answer)
