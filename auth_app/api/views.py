from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from auth_app.api.serializers import RegistrationSerializer
from auth_app.tasks import queue_activation_email
from auth_app.utils import (
    ACTIVATION_FAILED_MESSAGE,
    ACTIVATION_SUCCESS_MESSAGE,
    GENERIC_ERROR_MESSAGE,
    activate_user,
    build_registration_response,
)


class RegistrationView(APIView):
    """Register a new, inactive user and return an activation token."""

    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        """Validate the input, create the user and queue the activation email."""
        serializer = RegistrationSerializer(data=request.data)
        if not serializer.is_valid():
            return Response({'detail': GENERIC_ERROR_MESSAGE}, status=status.HTTP_400_BAD_REQUEST)
        user = serializer.save()
        queue_activation_email(user)
        return Response(build_registration_response(user), status=status.HTTP_201_CREATED)


class ActivationView(APIView):
    """Activate a user account via the uid and token from the email link."""

    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request, uidb64, token):
        """Activate the account or return a generic failure message."""
        if not activate_user(uidb64, token):
            return Response({'message': ACTIVATION_FAILED_MESSAGE}, status=status.HTTP_400_BAD_REQUEST)
        return Response({'message': ACTIVATION_SUCCESS_MESSAGE}, status=status.HTTP_200_OK)
