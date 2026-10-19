from datetime import datetime, timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.contrib.auth.tokens import PasswordResetTokenGenerator, default_token_generator
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from rest_framework import status
from rest_framework.test import APITestCase

from auth_app.utils import GENERIC_ERROR_MESSAGE, make_uidb64

VALID_EMAIL = 'user@example.com'
OLD_PASSWORD = 'oldsecurepassword'
NEW_PASSWORD = 'newsecurepassword'
SUCCESS_BODY = {'detail': 'Your Password has been successfully reset.'}
ERROR_BODY = {'detail': GENERIC_ERROR_MESSAGE}


class PasswordConfirmTestMixin:
    """Shared helpers for the password confirm endpoint tests."""

    def setUp(self):
        """Create an active user with uid and reset token like in the email link."""
        self.user = User.objects.create_user(
            username=VALID_EMAIL, email=VALID_EMAIL, password=OLD_PASSWORD)
        self.uidb64 = make_uidb64(self.user)
        self.token = default_token_generator.make_token(self.user)

    def confirm(self, data=None, uidb64=None, token=None, **kwargs):
        """Send a POST request to the password confirm endpoint."""
        payload = {'new_password': NEW_PASSWORD, 'confirm_password': NEW_PASSWORD}
        url = reverse('password_confirm', args=[uidb64 or self.uidb64, token or self.token])
        return self.client.post(url, payload if data is None else data, **kwargs)

    def assert_password(self, password):
        """Check that the user's current password is the given one."""
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(password))

    def assert_failed(self, response):
        """Check for 400, the generic message and an unchanged password."""
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data, ERROR_BODY)
        self.assert_password(OLD_PASSWORD)


class PasswordConfirmSuccessTests(PasswordConfirmTestMixin, APITestCase):
    """A valid link and matching passwords set the new password."""

    def test_returns_200_with_doc_body(self):
        """The response body matches the endpoint doc exactly."""
        response = self.confirm()
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, SUCCESS_BODY)

    def test_new_password_is_set(self):
        """Afterwards only the new password is valid."""
        self.confirm()
        self.assert_password(NEW_PASSWORD)
        self.assertFalse(self.user.check_password(OLD_PASSWORD))

    def test_password_is_stored_hashed(self):
        """The new password is never stored in plain text."""
        self.confirm()
        self.user.refresh_from_db()
        self.assertNotEqual(self.user.password, NEW_PASSWORD)

    def test_user_stays_active(self):
        """Resetting the password does not change the active state."""
        self.confirm()
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_active)

    def test_other_users_are_not_affected(self):
        """Only the user from the link gets the new password."""
        other = User.objects.create_user('o@example.com', 'o@example.com', OLD_PASSWORD)
        self.confirm()
        other.refresh_from_db()
        self.assertTrue(other.check_password(OLD_PASSWORD))

    def test_json_request(self):
        """A JSON body like the frontend sends works."""
        data = {'new_password': NEW_PASSWORD, 'confirm_password': NEW_PASSWORD}
        response = self.confirm(data, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_extra_fields_are_ignored(self):
        """Unknown fields change nothing."""
        data = {'new_password': NEW_PASSWORD, 'confirm_password': NEW_PASSWORD, 'is_staff': True}
        self.confirm(data)
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_staff)

    def test_url_path_matches_endpoint_doc(self):
        """The route is /api/password_confirm/<uidb64>/<token>/."""
        url = reverse('password_confirm', args=['abc', 'def-123'])
        self.assertEqual(url, '/api/password_confirm/abc/def-123/')


class PasswordConfirmPasswordVariantTests(PasswordConfirmTestMixin, APITestCase):
    """Special but allowed password values."""

    def confirm_with(self, password):
        """Confirm with the same password in both fields."""
        return self.confirm({'new_password': password, 'confirm_password': password})

    def test_same_password_as_before(self):
        """Setting the old password again is allowed."""
        response = self.confirm_with(OLD_PASSWORD)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_unicode_password(self):
        """Umlauts and symbols are kept exactly."""
        self.confirm_with('Pässwörd€!#')
        self.assert_password('Pässwörd€!#')

    def test_long_password(self):
        """A 128 character password is accepted."""
        self.confirm_with('a' * 128)
        self.assert_password('a' * 128)

    def test_password_is_case_sensitive(self):
        """Upper and lower case are not mixed up."""
        self.confirm_with('NewSecurePassword')
        self.user.refresh_from_db()
        self.assertFalse(self.user.check_password('newsecurepassword'))


class PasswordConfirmLoginFlowTests(PasswordConfirmTestMixin, APITestCase):
    """Login behaviour after a password reset."""

    def login(self, password):
        """Log in through the endpoint with the given password."""
        return self.client.post(reverse('login'), {'email': VALID_EMAIL, 'password': password})

    def test_login_with_new_password_works(self):
        """The new password logs the user in."""
        self.confirm()
        self.assertEqual(self.login(NEW_PASSWORD).status_code, status.HTTP_200_OK)

    def test_login_with_old_password_fails(self):
        """The old password is rejected."""
        self.confirm()
        self.assertEqual(self.login(OLD_PASSWORD).status_code, status.HTTP_400_BAD_REQUEST)

    def test_link_can_only_be_used_once(self):
        """A second use of the same link fails."""
        self.confirm()
        response = self.confirm({'new_password': 'third-pw', 'confirm_password': 'third-pw'})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assert_password(NEW_PASSWORD)


class PasswordConfirmInvalidLinkTests(PasswordConfirmTestMixin, APITestCase):
    """Invalid uid or token return 400 and change nothing."""

    def test_garbage_uid(self):
        """A uid that is no base64 is rejected."""
        self.assert_failed(self.confirm(uidb64='!!!'))

    def test_uid_of_unknown_user(self):
        """A uid of a non-existing user is rejected."""
        self.assert_failed(self.confirm(uidb64=urlsafe_base64_encode(force_bytes(999999))))

    def test_uid_that_is_no_number(self):
        """A valid base64 uid that decodes to text is rejected."""
        self.assert_failed(self.confirm(uidb64=urlsafe_base64_encode(b'abc')))

    def test_wrong_token(self):
        """A made-up token is rejected."""
        self.assert_failed(self.confirm(token='abc-123'))

    def test_token_of_other_user(self):
        """A token of another user does not work with this uid."""
        other = User.objects.create_user('o@example.com', 'o@example.com', 'pw')
        self.assert_failed(self.confirm(token=default_token_generator.make_token(other)))

    def test_uid_of_other_user(self):
        """Swapping the uid to another user does not work with this token."""
        other = User.objects.create_user('o@example.com', 'o@example.com', 'pw')
        response = self.confirm(uidb64=make_uidb64(other))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        other.refresh_from_db()
        self.assertTrue(other.check_password('pw'))

    def test_expired_token(self):
        """After 24 hours the link no longer works."""
        later = datetime.now() + timedelta(hours=24, minutes=1)
        with patch.object(PasswordResetTokenGenerator, '_now', return_value=later):
            self.assert_failed(self.confirm())

    def test_inactive_user(self):
        """A deactivated user cannot reset the password."""
        User.objects.filter(pk=self.user.pk).update(is_active=False)
        self.assert_failed(self.confirm())

    def test_deleted_user(self):
        """A link of a deleted user fails without a server error."""
        self.user.delete()
        response = self.confirm()
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data, ERROR_BODY)

    def test_token_invalid_after_last_login_changed(self):
        """A changed last_login invalidates the link, even with perfect input."""
        self.user.last_login = timezone.now()
        self.user.save()
        self.assert_failed(self.confirm())


class PasswordConfirmInvalidInputTests(PasswordConfirmTestMixin, APITestCase):
    """Invalid passwords return 400 and change nothing."""

    def test_passwords_do_not_match(self):
        """Different passwords are rejected."""
        data = {'new_password': NEW_PASSWORD, 'confirm_password': 'other'}
        self.assert_failed(self.confirm(data))

    def test_passwords_differ_only_in_case(self):
        """The comparison is case-sensitive."""
        data = {'new_password': NEW_PASSWORD, 'confirm_password': NEW_PASSWORD.upper()}
        self.assert_failed(self.confirm(data))

    def test_empty_body(self):
        """An empty body is rejected."""
        self.assert_failed(self.confirm({}))

    def test_missing_fields(self):
        """Each field is required."""
        for field in ['new_password', 'confirm_password']:
            with self.subTest(missing=field):
                self.assert_failed(self.confirm({field: NEW_PASSWORD}))

    def test_blank_passwords(self):
        """Empty passwords are rejected."""
        self.assert_failed(self.confirm({'new_password': '', 'confirm_password': ''}))

    def test_null_passwords(self):
        """Null values are rejected."""
        data = {'new_password': None, 'confirm_password': None}
        self.assert_failed(self.confirm(data, format='json'))

    def test_wrong_value_types(self):
        """Lists, objects and booleans are rejected."""
        for value in [[NEW_PASSWORD], {'pw': NEW_PASSWORD}, True]:
            with self.subTest(value=value):
                data = {'new_password': value, 'confirm_password': value}
                self.assert_failed(self.confirm(data, format='json'))

    def test_registration_field_names_are_not_accepted(self):
        """Only new_password and confirm_password are read."""
        data = {'password': NEW_PASSWORD, 'confirmed_password': NEW_PASSWORD}
        self.assert_failed(self.confirm(data))

    def test_malformed_json(self):
        """Broken JSON is rejected."""
        url = reverse('password_confirm', args=[self.uidb64, self.token])
        response = self.client.post(url, '{"new_password": ', content_type='application/json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assert_password(OLD_PASSWORD)


class PasswordConfirmWhitespaceTests(PasswordConfirmTestMixin, APITestCase):
    """Passwords must not contain any whitespace, like at registration."""

    def test_whitespace_anywhere_is_rejected(self):
        """Spaces, tabs and newlines inside or around the password fail."""
        for password in ['new pass', ' newpass', 'newpass ', 'new\tpass', 'new\npass', '   ']:
            with self.subTest(password=password):
                data = {'new_password': password, 'confirm_password': password}
                self.assert_failed(self.confirm(data))

    def test_whitespace_is_not_trimmed_into_a_match(self):
        """' pw' and 'pw' are not silently made equal."""
        data = {'new_password': NEW_PASSWORD, 'confirm_password': f' {NEW_PASSWORD} '}
        self.assert_failed(self.confirm(data))


class PasswordConfirmAuthTests(PasswordConfirmTestMixin, APITestCase):
    """The endpoint needs no authentication."""

    def test_invalid_access_cookie_does_not_block_confirm(self):
        """A broken access cookie must not cause a 401."""
        self.client.cookies['access_token'] = 'garbage'
        self.assertEqual(self.confirm().status_code, status.HTTP_200_OK)

    def test_other_methods_are_not_allowed(self):
        """GET, PUT, PATCH and DELETE return 405 and change nothing."""
        url = reverse('password_confirm', args=[self.uidb64, self.token])
        for method in ['get', 'put', 'patch', 'delete']:
            with self.subTest(method=method):
                response = getattr(self.client, method)(url)
                self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
        self.assert_password(OLD_PASSWORD)
