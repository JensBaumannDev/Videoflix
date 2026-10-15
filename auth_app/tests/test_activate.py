from django.contrib.auth.models import User
from django.contrib.auth.tokens import default_token_generator
from django.test import override_settings
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from rest_framework import status
from rest_framework.test import APITestCase

from auth_app.utils import ACTIVATION_FAILED_MESSAGE, make_uidb64

VALID_EMAIL = 'user@example.com'
SUCCESS_BODY = {'message': 'Account successfully activated.'}
FAILED_BODY = {'message': ACTIVATION_FAILED_MESSAGE}


class ActivateTestMixin:
    """Shared helpers for the activation endpoint tests."""

    def setUp(self):
        """Create an inactive user with a valid uid and token."""
        self.user = User.objects.create_user(
            username=VALID_EMAIL, email=VALID_EMAIL, password='x', is_active=False)
        self.uidb64 = make_uidb64(self.user)
        self.token = default_token_generator.make_token(self.user)

    def activate(self, uidb64=None, token=None):
        """Send a GET request to the activation endpoint."""
        url = reverse('activate', args=[uidb64 or self.uidb64, token or self.token])
        return self.client.get(url)

    def assert_failed(self, response):
        """Check for 400, the failure message and that the user stays inactive."""
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data, FAILED_BODY)
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_active)


class ActivateSuccessTests(ActivateTestMixin, APITestCase):
    """A valid uid and token activate the account."""

    def test_returns_200_with_doc_message(self):
        """The response matches the endpoint doc exactly."""
        response = self.activate()
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, SUCCESS_BODY)

    def test_user_is_active_afterwards(self):
        """is_active is set to True in the database."""
        self.activate()
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_active)

    def test_activation_changes_nothing_else(self):
        """Username, email, password and rights stay the same."""
        self.activate()
        self.user.refresh_from_db()
        self.assertEqual((self.user.username, self.user.email), (VALID_EMAIL, VALID_EMAIL))
        self.assertTrue(self.user.check_password('x'))
        self.assertFalse(self.user.is_staff or self.user.is_superuser)

    def test_works_without_authentication(self):
        """No cookie or token header is required."""
        self.client.logout()
        self.assertEqual(self.activate().status_code, status.HTTP_200_OK)

    def test_response_is_json(self):
        """The response uses the JSON content type."""
        self.assertEqual(self.activate()['Content-Type'], 'application/json')

    def test_url_path_matches_endpoint_doc(self):
        """The endpoint lives at /api/activate/<uidb64>/<token>/."""
        url = reverse('activate', args=['abc', 'def'])
        self.assertEqual(url, '/api/activate/abc/def/')


class ActivateInvalidTokenTests(ActivateTestMixin, APITestCase):
    """Wrong, foreign, reused or expired tokens are rejected."""

    def test_invalid_token(self):
        """A random token fails."""
        self.assert_failed(self.activate(token='invalid-token'))

    def test_token_of_other_user(self):
        """A token created for another user fails."""
        other = User.objects.create_user(username='o@example.com', email='o@example.com', password='y')
        self.assert_failed(self.activate(token=default_token_generator.make_token(other)))

    @override_settings(PASSWORD_RESET_TIMEOUT=-1)
    def test_expired_token(self):
        """A token older than PASSWORD_RESET_TIMEOUT fails."""
        self.assert_failed(self.activate())

    def test_token_invalid_after_password_change(self):
        """Changing the password invalidates the token."""
        self.user.set_password('new-password')
        self.user.save()
        self.assert_failed(self.activate())

    def test_second_activation_fails(self):
        """A link can only be used once."""
        self.activate()
        response = self.activate()
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data, FAILED_BODY)


class ActivateInvalidUidTests(ActivateTestMixin, APITestCase):
    """Broken or unknown uids are rejected without a server error."""

    def test_unknown_user_id(self):
        """A uid for a non-existing user fails."""
        self.assert_failed(self.activate(uidb64=urlsafe_base64_encode(force_bytes(999999))))

    def test_uid_of_other_user(self):
        """A valid token with another user's uid fails."""
        other = User.objects.create_user(username='o@example.com', email='o@example.com', password='y')
        self.assert_failed(self.activate(uidb64=make_uidb64(other)))

    def test_malformed_uids(self):
        """Non-base64, non-numeric, non-utf8 and huge ids return 400."""
        for raw in [b'abc', b'\xff\xfe', b'-1', b'99999999999999999999999']:
            with self.subTest(raw=raw):
                self.assert_failed(self.activate(uidb64=urlsafe_base64_encode(raw)))

    def test_uid_that_is_no_base64(self):
        """A uid with invalid base64 characters returns 400."""
        self.assert_failed(self.activate(uidb64='!!!'))

    def test_deleted_user(self):
        """The link of a deleted user fails."""
        self.user.delete()
        response = self.activate()
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data, FAILED_BODY)


class ActivateMethodTests(ActivateTestMixin, APITestCase):
    """Only GET is allowed on the activation endpoint."""

    def test_other_methods_are_not_allowed(self):
        """POST, PUT, PATCH and DELETE return 405 and activate nothing."""
        url = reverse('activate', args=[self.uidb64, self.token])
        for method in ['post', 'put', 'patch', 'delete']:
            with self.subTest(method=method):
                response = getattr(self.client, method)(url)
                self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_active)
