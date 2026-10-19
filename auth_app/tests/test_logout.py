from datetime import timedelta

import jwt
from django.contrib.auth.models import User
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken

VALID_EMAIL = 'user@example.com'
VALID_PASSWORD = 'securepassword'
TOKEN_COOKIES = ['access_token', 'refresh_token']
SUCCESS_BODY = {
    'detail': 'Logout successful! All tokens will be deleted. Refresh token is now invalid.'
}
MISSING_BODY = {'detail': 'Refresh token is missing.'}
INVALID_BODY = {'detail': 'Refresh token is invalid or expired.'}
WRONG_KEY = 'a-foreign-signing-key-of-32-bytes!'


class LogoutTestMixin:
    """Shared helpers for the logout endpoint tests."""

    def setUp(self):
        """Create an active user with a fresh refresh token."""
        self.user = User.objects.create_user(
            username=VALID_EMAIL, email=VALID_EMAIL, password=VALID_PASSWORD)
        self.refresh_token = RefreshToken.for_user(self.user)

    def set_cookies(self, refresh=None, access=None):
        """Put the given raw tokens into the client's cookie jar."""
        if refresh is not None:
            self.client.cookies['refresh_token'] = refresh
        if access is not None:
            self.client.cookies['access_token'] = access

    def logout(self, refresh=None, access=None, **kwargs):
        """Send a POST request to the logout endpoint with the given cookies."""
        self.set_cookies(refresh, access)
        return self.client.post(reverse('logout'), **kwargs)

    def is_blacklisted(self, token):
        """Return whether the refresh token's jti is on the blacklist."""
        return BlacklistedToken.objects.filter(token__jti=token['jti']).exists()

    def assert_cookies_deleted(self, response):
        """Check that both token cookies are cleared for the whole site."""
        for name in TOKEN_COOKIES:
            with self.subTest(cookie=name):
                cookie = response.cookies[name]
                self.assertEqual(cookie.value, '')
                self.assertEqual(cookie['max-age'], 0)
                self.assertEqual(cookie['path'], '/')
                self.assertEqual(cookie['samesite'], 'Lax')


class LogoutSuccessTests(LogoutTestMixin, APITestCase):
    """A valid refresh token cookie logs the user out."""

    def test_returns_200_with_doc_body(self):
        """The response body matches the endpoint doc exactly."""
        response = self.logout(str(self.refresh_token))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, SUCCESS_BODY)

    def test_refresh_token_is_blacklisted(self):
        """The refresh token from the cookie lands on the blacklist."""
        self.logout(str(self.refresh_token))
        self.assertTrue(self.is_blacklisted(self.refresh_token))

    def test_both_cookies_are_deleted(self):
        """access_token and refresh_token cookies are cleared."""
        response = self.logout(str(self.refresh_token))
        self.assert_cookies_deleted(response)

    def test_body_contains_no_tokens(self):
        """The logout response never echoes a token."""
        body = self.logout(str(self.refresh_token)).content.decode()
        self.assertNotIn(str(self.refresh_token), body)

    def test_response_is_json(self):
        """The response is sent as JSON."""
        response = self.logout(str(self.refresh_token))
        self.assertEqual(response['Content-Type'], 'application/json')

    def test_url_path_matches_endpoint_doc(self):
        """The route is exactly /api/logout/."""
        self.assertEqual(reverse('logout'), '/api/logout/')


class LogoutCookieVariantTests(LogoutTestMixin, APITestCase):
    """Logout depends only on the refresh token cookie."""

    def test_works_without_access_cookie(self):
        """The access token cookie is not required."""
        response = self.logout(str(self.refresh_token))
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_works_with_valid_access_cookie(self):
        """A valid access token cookie does not interfere."""
        access = str(self.refresh_token.access_token)
        response = self.logout(str(self.refresh_token), access)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_invalid_access_cookie_does_not_block_logout(self):
        """A broken access token must not cause a 401 on logout."""
        response = self.logout(str(self.refresh_token), 'garbage')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(self.is_blacklisted(self.refresh_token))

    def test_expired_access_cookie_does_not_block_logout(self):
        """An expired access token must not cause a 401 on logout."""
        access = AccessToken.for_user(self.user)
        access.set_exp(lifetime=-timedelta(seconds=1))
        response = self.logout(str(self.refresh_token), str(access))
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_body_is_ignored(self):
        """Extra JSON in the body changes nothing."""
        response = self.logout(str(self.refresh_token), data={'foo': 'bar'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_refresh_token_of_inactive_user(self):
        """An inactive user can still be logged out."""
        self.user.is_active = False
        self.user.save()
        response = self.logout(str(self.refresh_token))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(self.is_blacklisted(self.refresh_token))

    def test_refresh_token_of_deleted_user(self):
        """A token of a deleted user is still blacklisted without error."""
        self.user.delete()
        response = self.logout(str(self.refresh_token))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(self.is_blacklisted(self.refresh_token))


class LogoutMissingTokenTests(LogoutTestMixin, APITestCase):
    """Without a refresh token cookie the logout fails with 400."""

    def test_no_cookies_at_all(self):
        """No cookies return 400 with the missing message."""
        response = self.logout()
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data, MISSING_BODY)

    def test_empty_refresh_cookie(self):
        """An empty refresh cookie counts as missing."""
        response = self.logout('')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data, MISSING_BODY)

    def test_only_access_cookie(self):
        """An access cookie alone is not enough."""
        response = self.logout(access=str(self.refresh_token.access_token))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data, MISSING_BODY)

    def test_refresh_token_in_body_is_not_accepted(self):
        """The token is only read from the cookie, never from the body."""
        data = {'refresh': str(self.refresh_token)}
        response = self.logout(data=data, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(self.is_blacklisted(self.refresh_token))

    def test_cookies_are_deleted_anyway(self):
        """Even a failed logout clears the token cookies."""
        response = self.logout(access=str(self.refresh_token.access_token))
        self.assert_cookies_deleted(response)


class LogoutInvalidTokenTests(LogoutTestMixin, APITestCase):
    """Invalid refresh tokens fail with 400 and blacklist nothing."""

    def assert_invalid(self, raw_token):
        """Check for 400, the invalid message and cleared cookies."""
        response = self.logout(raw_token)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data, INVALID_BODY)
        self.assert_cookies_deleted(response)

    def test_garbage_token(self):
        """A random string is rejected."""
        self.assert_invalid('not-a-jwt')

    def test_token_signed_with_other_key(self):
        """A token with a foreign signature is rejected."""
        forged = jwt.encode(self.refresh_token.payload, WRONG_KEY, algorithm='HS256')
        self.assert_invalid(forged)
        self.assertFalse(BlacklistedToken.objects.exists())

    def test_expired_refresh_token(self):
        """An expired refresh token is rejected."""
        self.refresh_token.set_exp(lifetime=-timedelta(seconds=1))
        self.assert_invalid(str(self.refresh_token))

    def test_access_token_as_refresh_cookie(self):
        """An access token in the refresh cookie has the wrong type."""
        self.assert_invalid(str(self.refresh_token.access_token))
        self.assertFalse(BlacklistedToken.objects.exists())

    def test_already_blacklisted_token(self):
        """A second logout with the same token is rejected."""
        self.refresh_token.blacklist()
        self.assert_invalid(str(self.refresh_token))

    def test_unicode_garbage_token(self):
        """Non-ASCII junk does not crash the endpoint."""
        self.assert_invalid('äöü§°ß')


class LogoutFlowTests(LogoutTestMixin, APITestCase):
    """Logout in combination with login and token refresh."""

    def login(self):
        """Log in through the endpoint so the client stores the cookies."""
        payload = {'email': VALID_EMAIL, 'password': VALID_PASSWORD}
        return self.client.post(reverse('login'), payload)

    def test_login_then_logout(self):
        """Cookies set by login are enough to log out."""
        self.login()
        response = self.client.post(reverse('logout'))
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_refresh_after_logout_fails(self):
        """After logout the client has no refresh cookie left (400)."""
        self.login()
        self.client.post(reverse('logout'))
        response = self.client.post(reverse('token_refresh'))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_old_refresh_token_is_invalid_after_logout(self):
        """Reusing the blacklisted refresh token returns 401."""
        old_refresh = self.login().cookies['refresh_token'].value
        self.client.post(reverse('logout'))
        self.client.cookies['refresh_token'] = old_refresh
        response = self.client.post(reverse('token_refresh'))
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_second_logout_fails(self):
        """A second logout without cookies returns 400."""
        self.login()
        self.client.post(reverse('logout'))
        response = self.client.post(reverse('logout'))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_other_sessions_stay_valid(self):
        """Only the given refresh token is blacklisted."""
        other_token = RefreshToken.for_user(self.user)
        self.logout(str(self.refresh_token))
        self.assertFalse(self.is_blacklisted(other_token))


class LogoutMethodTests(LogoutTestMixin, APITestCase):
    """Only POST is allowed on the logout endpoint."""

    def test_other_methods_are_not_allowed(self):
        """GET, PUT, PATCH and DELETE return 405."""
        self.set_cookies(str(self.refresh_token))
        for method in ['get', 'put', 'patch', 'delete']:
            with self.subTest(method=method):
                response = getattr(self.client, method)(reverse('logout'))
                self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
        self.assertFalse(self.is_blacklisted(self.refresh_token))
