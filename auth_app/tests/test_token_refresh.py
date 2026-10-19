from datetime import timedelta

import jwt
from django.conf import settings
from django.contrib.auth.models import User
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken

VALID_EMAIL = 'user@example.com'
VALID_PASSWORD = 'securepassword'
MISSING_BODY = {'detail': 'Refresh token is missing.'}
INVALID_BODY = {'detail': 'Refresh token is invalid or expired.'}
WRONG_KEY = 'a-foreign-signing-key-of-32-bytes!'


class TokenRefreshTestMixin:
    """Shared helpers for the token refresh endpoint tests."""

    def setUp(self):
        """Create an active user with a fresh refresh token."""
        self.user = User.objects.create_user(
            username=VALID_EMAIL, email=VALID_EMAIL, password=VALID_PASSWORD)
        self.refresh_token = RefreshToken.for_user(self.user)

    def refresh(self, refresh=None, access=None, **kwargs):
        """Send a POST request to the refresh endpoint with the given cookies."""
        if refresh is not None:
            self.client.cookies['refresh_token'] = refresh
        if access is not None:
            self.client.cookies['access_token'] = access
        return self.client.post(reverse('token_refresh'), **kwargs)

    def assert_new_access_for_user(self, raw_access):
        """Check that the raw token is a valid access token of the user."""
        access = AccessToken(raw_access)
        self.assertEqual(str(access['user_id']), str(self.user.id))


class TokenRefreshSuccessTests(TokenRefreshTestMixin, APITestCase):
    """A valid refresh token cookie returns a new access token."""

    def test_returns_200_with_doc_body(self):
        """The body has the doc's detail text and the new access token."""
        response = self.refresh(str(self.refresh_token))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(set(response.data), {'detail', 'access'})
        self.assertEqual(response.data['detail'], 'Token refreshed')

    def test_body_access_is_valid_token_for_user(self):
        """The access token in the body belongs to the user."""
        response = self.refresh(str(self.refresh_token))
        self.assert_new_access_for_user(response.data['access'])

    def test_sets_access_cookie(self):
        """A new access_token cookie with the same token is set."""
        response = self.refresh(str(self.refresh_token))
        cookie = response.cookies['access_token']
        self.assertEqual(cookie.value, response.data['access'])
        self.assert_new_access_for_user(cookie.value)

    def test_access_cookie_is_httponly_lax_and_not_secure(self):
        """The cookie has the same flags as the login cookies."""
        cookie = self.refresh(str(self.refresh_token)).cookies['access_token']
        self.assertTrue(cookie['httponly'])
        self.assertEqual(cookie['samesite'], 'Lax')
        self.assertFalse(cookie['secure'])
        self.assertEqual(cookie['path'], '/')

    def test_access_cookie_max_age_matches_token_lifetime(self):
        """The cookie expires together with the access token."""
        cookie = self.refresh(str(self.refresh_token)).cookies['access_token']
        lifetime = settings.SIMPLE_JWT['ACCESS_TOKEN_LIFETIME']
        self.assertEqual(cookie['max-age'], int(lifetime.total_seconds()))

    def test_refresh_cookie_is_not_touched(self):
        """Without rotation the refresh cookie stays as it is."""
        response = self.refresh(str(self.refresh_token))
        self.assertNotIn('refresh_token', response.cookies)

    def test_refresh_token_is_not_in_body(self):
        """The refresh token is never echoed in the body."""
        body = self.refresh(str(self.refresh_token)).content.decode()
        self.assertNotIn(str(self.refresh_token), body)
        self.assertNotIn('"refresh"', body)

    def test_response_is_json(self):
        """The response is sent as JSON."""
        response = self.refresh(str(self.refresh_token))
        self.assertEqual(response['Content-Type'], 'application/json')

    def test_url_path_matches_endpoint_doc(self):
        """The route is exactly /api/token/refresh/."""
        self.assertEqual(reverse('token_refresh'), '/api/token/refresh/')


class TokenRefreshCookieVariantTests(TokenRefreshTestMixin, APITestCase):
    """Refresh depends only on the refresh token cookie."""

    def test_expired_access_cookie_does_not_block_refresh(self):
        """An expired access token is the normal case and must not cause 401."""
        access = AccessToken.for_user(self.user)
        access.set_exp(lifetime=-timedelta(seconds=1))
        response = self.refresh(str(self.refresh_token), str(access))
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_garbage_access_cookie_does_not_block_refresh(self):
        """A broken access cookie is simply replaced."""
        response = self.refresh(str(self.refresh_token), 'garbage')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertNotEqual(response.cookies['access_token'].value, 'garbage')

    def test_refresh_can_be_repeated(self):
        """The same refresh token works for several refreshes."""
        self.refresh(str(self.refresh_token))
        response = self.refresh(str(self.refresh_token))
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_body_is_ignored(self):
        """Extra JSON in the body changes nothing."""
        response = self.refresh(str(self.refresh_token), data={'foo': 'bar'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_refresh_after_login(self):
        """Cookies set by login are enough to refresh."""
        payload = {'email': VALID_EMAIL, 'password': VALID_PASSWORD}
        self.client.post(reverse('login'), payload)
        response = self.client.post(reverse('token_refresh'))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assert_new_access_for_user(response.cookies['access_token'].value)


class TokenRefreshMissingTokenTests(TokenRefreshTestMixin, APITestCase):
    """Without a refresh token cookie the refresh fails with 400."""

    def assert_missing(self, response):
        """Check for 400, the missing message and no new access cookie."""
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data, MISSING_BODY)
        self.assertNotIn('access_token', response.cookies)

    def test_no_cookies_at_all(self):
        """No cookies return 400."""
        self.assert_missing(self.refresh())

    def test_empty_refresh_cookie(self):
        """An empty refresh cookie counts as missing."""
        self.assert_missing(self.refresh(''))

    def test_only_access_cookie(self):
        """A valid access cookie alone is not enough."""
        self.assert_missing(self.refresh(access=str(self.refresh_token.access_token)))

    def test_refresh_token_in_body_is_not_accepted(self):
        """The token is only read from the cookie, never from the body."""
        data = {'refresh': str(self.refresh_token)}
        self.assert_missing(self.refresh(data=data, format='json'))


class TokenRefreshInvalidTokenTests(TokenRefreshTestMixin, APITestCase):
    """Invalid refresh tokens fail with 401 and set no cookie."""

    def assert_invalid(self, raw_token):
        """Check for 401, the invalid message and no new access cookie."""
        response = self.refresh(raw_token)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(response.data, INVALID_BODY)
        self.assertNotIn('access_token', response.cookies)

    def test_garbage_token(self):
        """A random string is rejected."""
        self.assert_invalid('not-a-jwt')

    def test_unicode_garbage_token(self):
        """Non-ASCII junk does not crash the endpoint."""
        self.assert_invalid('äöü§°ß')

    def test_token_signed_with_other_key(self):
        """A token with a foreign signature is rejected."""
        forged = jwt.encode(self.refresh_token.payload, WRONG_KEY, algorithm='HS256')
        self.assert_invalid(forged)

    def test_tampered_payload(self):
        """A token whose payload was changed to another user is rejected."""
        other = User.objects.create_user(username='o@example.com', password='pw')
        header, _, signature = str(self.refresh_token).split('.')
        fake = RefreshToken.for_user(other)
        self.assert_invalid(f"{header}.{str(fake).split('.')[1]}.{signature}")

    def test_expired_refresh_token(self):
        """An expired refresh token is rejected."""
        self.refresh_token.set_exp(lifetime=-timedelta(seconds=1))
        self.assert_invalid(str(self.refresh_token))

    def test_access_token_as_refresh_cookie(self):
        """An access token in the refresh cookie has the wrong type."""
        self.assert_invalid(str(self.refresh_token.access_token))

    def test_blacklisted_refresh_token(self):
        """A blacklisted (logged out) refresh token is rejected."""
        self.refresh_token.blacklist()
        self.assert_invalid(str(self.refresh_token))


class TokenRefreshUserStateTests(TokenRefreshTestMixin, APITestCase):
    """The refresh token's user must still exist and be active."""

    def assert_unauthorized(self, response):
        """Check for 401 with the invalid message."""
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(response.data, INVALID_BODY)

    def test_inactive_user(self):
        """A deactivated user gets no new access token."""
        self.user.is_active = False
        self.user.save()
        self.assert_unauthorized(self.refresh(str(self.refresh_token)))

    def test_deleted_user(self):
        """A deleted user gets 401 instead of a server error."""
        self.user.delete()
        self.assert_unauthorized(self.refresh(str(self.refresh_token)))

    def test_token_without_user_id(self):
        """A validly signed token without user claim is rejected."""
        token = RefreshToken()
        self.assert_unauthorized(self.refresh(str(token)))


class TokenRefreshMethodTests(TokenRefreshTestMixin, APITestCase):
    """Only POST is allowed on the refresh endpoint."""

    def test_other_methods_are_not_allowed(self):
        """GET, PUT, PATCH and DELETE return 405."""
        self.client.cookies['refresh_token'] = str(self.refresh_token)
        for method in ['get', 'put', 'patch', 'delete']:
            with self.subTest(method=method):
                response = getattr(self.client, method)(reverse('token_refresh'))
                self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
