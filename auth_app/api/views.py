from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from auth_app.api.serializers import RegistrationSerializer
from auth_app.utils import GENERIC_ERROR_MESSAGE, build_registration_response


class RegistrationView(APIView):
    """Register a new, inactive user and return an activation token."""

    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        """Validate the input and create the user, or return a generic error."""
        serializer = RegistrationSerializer(data=request.data)
        if not serializer.is_valid():
            return Response({'detail': GENERIC_ERROR_MESSAGE}, status=status.HTTP_400_BAD_REQUEST)
        user = serializer.save()
        return Response(build_registration_response(user), status=status.HTTP_201_CREATED)
