from rest_framework import serializers


class CopilotContextSerializer(serializers.Serializer):
    screen = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    record_type = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    record_id = serializers.CharField(required=False, allow_blank=True, allow_null=True)


class CopilotChatRequestSerializer(serializers.Serializer):
    message = serializers.CharField(max_length=1000, trim_whitespace=True)
    context = CopilotContextSerializer(required=False)


class CopilotCardSerializer(serializers.Serializer):
    type = serializers.CharField()
    title = serializers.CharField()
    subtitle = serializers.CharField(allow_blank=True)
    status = serializers.CharField(allow_blank=True)
    url = serializers.CharField(allow_blank=True)


class CopilotChatResponseSerializer(serializers.Serializer):
    answer = serializers.CharField()
    cards = CopilotCardSerializer(many=True)
    suggested_questions = serializers.ListField(child=serializers.CharField())
    safety_notice = serializers.CharField()
