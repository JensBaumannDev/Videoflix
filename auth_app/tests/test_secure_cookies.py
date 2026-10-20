from django.conf import settings
from django.contrib.auth.models import User
from django.test import override_settings
from django.urls import reverse
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

VALID_EMAIL = 'user@example.com'
VALID_PASSWORD = 'securepassword'
TOKEN_COOKIES = ['access_token', 'refresh_token']


class SecureCookieSettingTests(APITestCase):
    """COOKIE_SECURE switches the Secure flag for all cookies of the project."""

    def test_cookies_are_not_secure_by_default(self):
        """Locally (http) the flag is off, otherwise browsers drop the cookies."""
        self.assertFalse(settings.COOKIE_SECURE)

    def test_session_and_csrf_cookies_follow_the_setting(self):
        """The admin login cookies use the same flag as the JWT cookies."""
        self.assertEqual(settings.SESSION_COOKIE_SECURE, settings.COOKIE_SECURE)
        self.assertEqual(settings.CSRF_COOKIE_SECURE, settings.COOKIE_SECURE)


class SecureJwtCookieTests(APITestCase):
    """With COOKIE_SECURE the JWT cookies are only sent over HTTPS."""

    def setUp(self):
        """Create an active user."""
        self.user = User.objects.create_user(VALID_EMAIL, VALID_EMAIL, VALID_PASSWORD)

    def login(self):
        """Log in through the endpoint and return the response."""
        payload = {'email': VALID_EMAIL, 'password': VALID_PASSWORD}
        return self.client.post(reverse('login'), payload)

    @override_settings(COOKIE_SECURE=True)
    def test_login_cookies_are_secure(self):
        """Both login cookies get the Secure flag."""
        response = self.login()
        for name in TOKEN_COOKIES:
            with self.subTest(cookie=name):
                self.assertTrue(response.cookies[name]['secure'])

    @override_settings(COOKIE_SECURE=True)
    def test_refreshed_access_cookie_is_secure(self):
        """The new access cookie from token/refresh is secure too."""
        self.client.cookies['refresh_token'] = str(RefreshToken.for_user(self.user))
        response = self.client.post(reverse('token_refresh'))
        self.assertTrue(response.cookies['access_token']['secure'])

    @override_settings(COOKIE_SECURE=True)
    def test_other_cookie_flags_stay_the_same(self):
        """HttpOnly and SameSite=Lax are kept in production."""
        cookie = self.login().cookies['access_token']
        self.assertTrue(cookie['httponly'])
        self.assertEqual(cookie['samesite'], 'Lax')

    @override_settings(COOKIE_SECURE=False)
    def test_cookies_are_not_secure_when_disabled(self):
        """Without the setting the cookies work over plain http."""
        response = self.login()
        for name in TOKEN_COOKIES:
            with self.subTest(cookie=name):
                self.assertFalse(response.cookies[name]['secure'])
