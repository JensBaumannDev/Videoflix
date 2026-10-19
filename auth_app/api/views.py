from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from auth_app.api.serializers import LoginSerializer, RegistrationSerializer
from auth_app.tasks import queue_activation_email
from auth_app.utils import (
    ACTIVATION_FAILED_MESSAGE,
    ACTIVATION_SUCCESS_MESSAGE,
    ACCESS_COOKIE_NAME,
    GENERIC_ERROR_MESSAGE,
    LOGOUT_SUCCESS_MESSAGE,
    REFRESH_COOKIE_NAME,
    REFRESH_INVALID_MESSAGE,
    REFRESH_MISSING_MESSAGE,
    activate_user,
    blacklist_refresh_token,
    build_login_response,
    build_refresh_response,
    build_registration_response,
    delete_token_cookies,
    get_valid_refresh_token,
    set_login_cookies,
    set_token_cookie,
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


class LoginView(APIView):
    """Log in an active user and set the JWTs as HttpOnly cookies."""

    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        """Check the credentials and return the user with token cookies."""
        serializer = LoginSerializer(data=request.data)
        if not serializer.is_valid():
            return Response({'detail': GENERIC_ERROR_MESSAGE}, status=status.HTTP_400_BAD_REQUEST)
        user = serializer.validated_data['user']
        response = Response(build_login_response(user), status=status.HTTP_200_OK)
        set_login_cookies(response, user)
        return response


class LogoutView(APIView):
    """Blacklist the refresh token and delete both token cookies."""

    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        """Log out with the refresh token cookie; cookies are deleted in any case."""
        raw_token = request.COOKIES.get(REFRESH_COOKIE_NAME)
        if not raw_token:
            response = Response({'detail': REFRESH_MISSING_MESSAGE}, status=status.HTTP_400_BAD_REQUEST)
        elif not blacklist_refresh_token(raw_token):
            response = Response({'detail': REFRESH_INVALID_MESSAGE}, status=status.HTTP_400_BAD_REQUEST)
        else:
            response = Response({'detail': LOGOUT_SUCCESS_MESSAGE}, status=status.HTTP_200_OK)
        delete_token_cookies(response)
        return response


class CookieTokenRefreshView(APIView):
    """Issue a new access token cookie from the refresh token cookie."""

    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        """Return a new access token or 400/401 for a missing/invalid refresh token."""
        raw_token = request.COOKIES.get(REFRESH_COOKIE_NAME)
        if not raw_token:
            return Response({'detail': REFRESH_MISSING_MESSAGE}, status=status.HTTP_400_BAD_REQUEST)
        refresh_token = get_valid_refresh_token(raw_token)
        if refresh_token is None:
            return Response({'detail': REFRESH_INVALID_MESSAGE}, status=status.HTTP_401_UNAUTHORIZED)
        access_token = refresh_token.access_token
        response = Response(build_refresh_response(access_token), status=status.HTTP_200_OK)
        set_token_cookie(response, ACCESS_COOKIE_NAME, access_token)
        return response
