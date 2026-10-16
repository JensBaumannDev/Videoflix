from datetime import timedelta

import jwt
from django.conf import settings
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory, APITestCase
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import AuthenticationFailed, InvalidToken
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken

from auth_app.api.authentication import CookieJWTAuthentication

VALID_EMAIL = 'user@example.com'


class ProtectedTestView(APIView):
    """Minimal protected view to test the authentication class end to end."""

    authentication_classes = [CookieJWTAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        """Return the id of the authenticated user."""
        return Response({'user_id': request.user.id})


class CookieAuthTestMixin:
    """Shared helpers for the cookie authentication tests."""

    def setUp(self):
        """Create an active user and a request factory."""
        self.user = User.objects.create_user(username=VALID_EMAIL, email=VALID_EMAIL, password='x')
        self.factory = APIRequestFactory()

    def make_request(self, cookies=None, **headers):
        """Build a GET request carrying the given cookies and headers."""
        request = self.factory.get('/protected/', **headers)
        request.COOKIES.update(cookies or {})
        return request

    def call_view(self, cookies=None, **headers):
        """Call the protected test view with the given cookies."""
        return ProtectedTestView.as_view()(self.make_request(cookies, **headers))

    def authenticate(self, cookies=None, **headers):
        """Run the authentication class directly on a request."""
        return CookieJWTAuthentication().authenticate(self.make_request(cookies, **headers))


class CookieAuthSuccessTests(CookieAuthTestMixin, TestCase):
    """A valid access_token cookie authenticates the user."""

    def test_returns_user_and_token(self):
        """authenticate() returns the user and the validated token."""
        token = AccessToken.for_user(self.user)
        user, validated = self.authenticate({'access_token': str(token)})
        self.assertEqual(user, self.user)
        self.assertEqual(str(validated), str(token))

    def test_protected_view_returns_200(self):
        """The protected view is reachable with a valid cookie."""
        response = self.call_view({'access_token': str(AccessToken.for_user(self.user))})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, {'user_id': self.user.id})

    def test_cookie_wins_over_missing_header(self):
        """No Authorization header is needed, the cookie is enough."""
        result = self.authenticate({'access_token': str(AccessToken.for_user(self.user))})
        self.assertIsNotNone(result)


class CookieAuthMissingTokenTests(CookieAuthTestMixin, TestCase):
    """Requests without an access_token cookie are anonymous."""

    def test_no_cookie_returns_none(self):
        """authenticate() returns None so other checks can decide."""
        self.assertIsNone(self.authenticate())

    def test_empty_cookie_returns_none(self):
        """An empty cookie value counts as missing."""
        self.assertIsNone(self.authenticate({'access_token': ''}))

    def test_protected_view_returns_401_without_cookie(self):
        """Without cookie the protected view answers 401, not 403."""
        self.assertEqual(self.call_view().status_code, status.HTTP_401_UNAUTHORIZED)

    def test_bearer_header_is_not_used(self):
        """A token in the Authorization header alone does not log in."""
        header = f'Bearer {AccessToken.for_user(self.user)}'
        response = self.call_view(HTTP_AUTHORIZATION=header)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_refresh_cookie_alone_is_not_enough(self):
        """Only the access_token cookie authenticates requests."""
        response = self.call_view({'refresh_token': str(RefreshToken.for_user(self.user))})
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class CookieAuthInvalidTokenTests(CookieAuthTestMixin, TestCase):
    """Broken, expired or wrong tokens are rejected with 401."""

    def assert_rejected(self, token_value):
        """Check that the cookie value is rejected by class and view."""
        with self.assertRaises((InvalidToken, AuthenticationFailed)):
            self.authenticate({'access_token': token_value})
        response = self.call_view({'access_token': token_value})
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_garbage_token(self):
        """A random string is rejected."""
        self.assert_rejected('not-a-jwt')

    def test_expired_token(self):
        """An access token past its lifetime is rejected."""
        token = AccessToken.for_user(self.user)
        token.set_exp(lifetime=-timedelta(seconds=1))
        self.assert_rejected(str(token))

    def test_refresh_token_in_access_cookie(self):
        """A refresh token is not accepted as access token."""
        self.assert_rejected(str(RefreshToken.for_user(self.user)))

    def test_tampered_token(self):
        """A token with a changed signature is rejected."""
        self.assert_rejected(str(AccessToken.for_user(self.user))[:-2] + 'xx')

    def test_token_of_deleted_user(self):
        """A token for a user that no longer exists is rejected."""
        token = str(AccessToken.for_user(self.user))
        self.user.delete()
        self.assert_rejected(token)

    def test_token_of_inactive_user(self):
        """A token for a deactivated user is rejected."""
        token = str(AccessToken.for_user(self.user))
        self.user.is_active = False
        self.user.save()
        self.assert_rejected(token)


class CookieAuthForgedTokenTests(CookieAuthTestMixin, TestCase):
    """Hand-made or wrongly formatted tokens are rejected."""

    def assert_rejected(self, token_value):
        """Check that the protected view answers 401 for the cookie."""
        response = self.call_view({'access_token': token_value})
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_token_signed_with_other_key(self):
        """A token signed with a foreign secret is rejected."""
        payload = dict(AccessToken.for_user(self.user).payload)
        foreign_key = 'foreign-secret-key-with-enough-length'
        self.assert_rejected(jwt.encode(payload, foreign_key, algorithm='HS256'))

    def test_token_without_user_id(self):
        """A valid token without user claim is rejected."""
        token = AccessToken.for_user(self.user)
        del token['user_id']
        self.assert_rejected(str(token))

    def test_bearer_prefix_in_cookie(self):
        """The cookie holds the plain token, not 'Bearer <token>'."""
        self.assert_rejected(f'Bearer {AccessToken.for_user(self.user)}')

    def test_whitespace_cookie(self):
        """A cookie with only spaces is rejected."""
        self.assert_rejected('   ')

    def test_valid_header_does_not_rescue_invalid_cookie(self):
        """An invalid cookie fails even with a valid Bearer header."""
        header = f'Bearer {AccessToken.for_user(self.user)}'
        response = self.call_view({'access_token': 'broken'}, HTTP_AUTHORIZATION=header)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class CookieAuthLoginIntegrationTests(APITestCase):
    """The cookie set by the login endpoint opens protected views."""

    def test_login_cookie_authenticates_request(self):
        """After login the access_token cookie identifies the user."""
        user = User.objects.create_user(username=VALID_EMAIL, email=VALID_EMAIL, password='x')
        login = self.client.post(reverse('login'), {'email': VALID_EMAIL, 'password': 'x'})
        request = APIRequestFactory().get('/protected/')
        request.COOKIES['access_token'] = login.cookies['access_token'].value
        response = ProtectedTestView.as_view()(request)
        self.assertEqual(response.data, {'user_id': user.id})


class CookieAuthSettingsTests(TestCase):
    """The cookie class is the project's default authentication."""

    def test_is_default_authentication_class(self):
        """REST_FRAMEWORK uses the cookie class by default."""
        defaults = settings.REST_FRAMEWORK['DEFAULT_AUTHENTICATION_CLASSES']
        self.assertEqual(defaults, ['auth_app.api.authentication.CookieJWTAuthentication'])
