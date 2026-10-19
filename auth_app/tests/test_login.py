from django.contrib.auth.models import User
from django.contrib.auth.tokens import default_token_generator
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework_simplejwt.token_blacklist.models import OutstandingToken
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken

from auth_app.utils import GENERIC_ERROR_MESSAGE, make_uidb64

VALID_EMAIL = 'user@example.com'
VALID_PASSWORD = 'securepassword'
ERROR_BODY = {'detail': GENERIC_ERROR_MESSAGE}
TOKEN_COOKIES = ['access_token', 'refresh_token']
FLOW_EMAIL = 'new@example.com'
FLOW_PASSWORD = 'pw12345!'


class LoginTestMixin:
    """Shared helpers for the login endpoint tests."""

    def setUp(self):
        """Create an active user that registered with email as username."""
        self.user = User.objects.create_user(
            username=VALID_EMAIL, email=VALID_EMAIL, password=VALID_PASSWORD)

    def login(self, data=None, **kwargs):
        """Send a POST request to the login endpoint."""
        payload = {'email': VALID_EMAIL, 'password': VALID_PASSWORD} if data is None else data
        return self.client.post(reverse('login'), payload, **kwargs)

    def assert_failed(self, response):
        """Check for 400, the generic message and that no cookie is set."""
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data, ERROR_BODY)
        for name in TOKEN_COOKIES:
            self.assertNotIn(name, response.cookies)


class LoginSuccessTests(LoginTestMixin, APITestCase):
    """Valid credentials of an active user log the user in."""

    def test_returns_200_with_doc_body(self):
        """The response body matches the endpoint doc exactly."""
        response = self.login()
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        expected = {'detail': 'Login successful',
                    'user': {'id': self.user.id, 'username': VALID_EMAIL}}
        self.assertEqual(response.data, expected)

    def test_body_contains_no_tokens(self):
        """Tokens are only sent as cookies, never in the body."""
        body = self.login().content.decode()
        for name in TOKEN_COOKIES + ['access', 'refresh']:
            self.assertNotIn(f'"{name}"', body)

    def test_sets_both_token_cookies(self):
        """access_token and refresh_token cookies are set."""
        response = self.login()
        for name in TOKEN_COOKIES:
            self.assertIn(name, response.cookies)
            self.assertTrue(response.cookies[name].value)

    def test_cookies_are_httponly_lax_and_not_secure(self):
        """Cookies are HttpOnly, SameSite=Lax and work over http in dev."""
        response = self.login()
        for name in TOKEN_COOKIES:
            with self.subTest(cookie=name):
                cookie = response.cookies[name]
                self.assertTrue(cookie['httponly'])
                self.assertEqual(cookie['samesite'], 'Lax')
                self.assertFalse(cookie['secure'])

    def test_cookies_are_valid_tokens_for_the_user(self):
        """Both cookies contain valid JWTs for the logged-in user."""
        response = self.login()
        access = AccessToken(response.cookies['access_token'].value)
        refresh = RefreshToken(response.cookies['refresh_token'].value)
        self.assertEqual(str(access['user_id']), str(self.user.id))
        self.assertEqual(str(refresh['user_id']), str(self.user.id))

    def test_cookies_are_sent_for_the_whole_site(self):
        """Cookies use path / so every API request includes them."""
        response = self.login()
        for name in TOKEN_COOKIES:
            self.assertEqual(response.cookies[name]['path'], '/')

    def test_response_is_json(self):
        """The response uses the JSON content type."""
        self.assertEqual(self.login()['Content-Type'], 'application/json')

    def test_url_path_matches_endpoint_doc(self):
        """The endpoint lives at /api/login/."""
        self.assertEqual(reverse('login'), '/api/login/')


class LoginInputVariantTests(LoginTestMixin, APITestCase):
    """Accepted variations of a valid login request."""

    def test_email_is_case_insensitive(self):
        """Upper-case letters in the email still log the user in."""
        response = self.login({'email': 'User@Example.COM', 'password': VALID_PASSWORD})
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_json_request(self):
        """A JSON body like the frontend sends works."""
        data = {'email': VALID_EMAIL, 'password': VALID_PASSWORD}
        self.assertEqual(self.login(data, format='json').status_code, status.HTTP_200_OK)

    def test_extra_fields_are_ignored(self):
        """Unknown fields do not break the login."""
        data = {'email': VALID_EMAIL, 'password': VALID_PASSWORD, 'remember': True}
        self.assertEqual(self.login(data).status_code, status.HTTP_200_OK)

    def test_invalid_old_access_cookie_does_not_block_login(self):
        """An expired or broken access_token cookie is ignored on login."""
        self.client.cookies['access_token'] = 'broken-token'
        self.assertEqual(self.login().status_code, status.HTTP_200_OK)

    def test_login_twice_works(self):
        """A user can log in again while already logged in."""
        self.login()
        self.assertEqual(self.login().status_code, status.HTTP_200_OK)


class LoginWrongCredentialsTests(LoginTestMixin, APITestCase):
    """Wrong credentials and inactive users get the same generic error."""

    def test_wrong_password(self):
        """A wrong password returns 400 with the generic message."""
        self.assert_failed(self.login({'email': VALID_EMAIL, 'password': 'wrong'}))

    def test_unknown_email(self):
        """An unregistered email returns the very same response."""
        self.assert_failed(self.login({'email': 'nobody@example.com', 'password': VALID_PASSWORD}))

    def test_inactive_user(self):
        """A not yet activated user cannot log in."""
        self.user.is_active = False
        self.user.save()
        self.assert_failed(self.login())

    def test_password_is_case_sensitive(self):
        """The password must match exactly."""
        self.assert_failed(self.login({'email': VALID_EMAIL, 'password': VALID_PASSWORD.upper()}))

    def test_login_with_username_field_fails(self):
        """Only the email field is accepted, not username."""
        self.assert_failed(self.login({'username': VALID_EMAIL, 'password': VALID_PASSWORD}))

    def test_errors_do_not_reveal_the_reason(self):
        """Unknown email, wrong password and inactive user look identical."""
        unknown = self.login({'email': 'nobody@example.com', 'password': 'x'})
        wrong = self.login({'email': VALID_EMAIL, 'password': 'x'})
        self.user.is_active = False
        self.user.save()
        inactive = self.login()
        self.assertEqual(unknown.data, wrong.data)
        self.assertEqual(wrong.data, inactive.data)


class LoginInvalidInputTests(LoginTestMixin, APITestCase):
    """Missing or malformed fields return the generic error."""

    def test_empty_body(self):
        """An empty request returns 400."""
        self.assert_failed(self.login({}))

    def test_missing_fields(self):
        """Missing email or password returns 400."""
        for data in [{'password': VALID_PASSWORD}, {'email': VALID_EMAIL}]:
            with self.subTest(data=data):
                self.assert_failed(self.login(data))

    def test_blank_fields(self):
        """Blank email or password returns 400."""
        blank_variants = [
            {'email': '', 'password': VALID_PASSWORD},
            {'email': VALID_EMAIL, 'password': ''},
        ]
        for data in blank_variants:
            with self.subTest(data=data):
                self.assert_failed(self.login(data))

    def test_invalid_email_format(self):
        """An email without @ returns 400."""
        self.assert_failed(self.login({'email': 'not-an-email', 'password': VALID_PASSWORD}))

    def test_null_values(self):
        """null for email and password returns 400."""
        data = {'email': None, 'password': None}
        self.assert_failed(self.login(data, format='json'))


class LoginMethodTests(LoginTestMixin, APITestCase):
    """Only POST is allowed on the login endpoint."""

    def test_other_methods_are_not_allowed(self):
        """GET, PUT, PATCH and DELETE return 405."""
        for method in ['get', 'put', 'patch', 'delete']:
            with self.subTest(method=method):
                response = getattr(self.client, method)(reverse('login'))
                self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)


class LoginTokenTests(LoginTestMixin, APITestCase):
    """Lifetime and bookkeeping of the issued tokens."""

    def test_cookie_max_age_matches_token_lifetime(self):
        """Cookies expire together with their tokens (30 min / 1 day)."""
        response = self.login()
        self.assertEqual(int(response.cookies['access_token']['max-age']), 30 * 60)
        self.assertEqual(int(response.cookies['refresh_token']['max-age']), 24 * 60 * 60)

    def test_access_token_outlives_frontend_refresh_interval(self):
        """The frontend refreshes every 20 minutes, so access lives longer."""
        access = AccessToken(self.login().cookies['access_token'].value)
        self.assertGreater(access['exp'] - access['iat'], 20 * 60)

    def test_refresh_token_is_stored_for_blacklisting(self):
        """The refresh token is recorded so logout can blacklist it later."""
        refresh = RefreshToken(self.login().cookies['refresh_token'].value)
        stored = OutstandingToken.objects.filter(jti=refresh['jti'], user=self.user)
        self.assertTrue(stored.exists())

    def test_each_login_issues_new_tokens(self):
        """Two logins never share the same tokens."""
        first, second = self.login(), self.login()
        for name in TOKEN_COOKIES:
            self.assertNotEqual(first.cookies[name].value, second.cookies[name].value)


class LoginSpecialInputTests(LoginTestMixin, APITestCase):
    """Edge cases in the submitted values."""

    def test_whitespace_around_email_is_ignored(self):
        """Leading and trailing spaces in the email are trimmed."""
        response = self.login({'email': f'  {VALID_EMAIL} ', 'password': VALID_PASSWORD})
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_whitespace_in_password_is_not_trimmed(self):
        """A password with extra spaces is a different password."""
        self.assert_failed(self.login({'email': VALID_EMAIL, 'password': f' {VALID_PASSWORD} '}))

    def test_unicode_password(self):
        """Passwords with umlauts and emojis work."""
        self.user.set_password('Pässwörd🔒123')
        self.user.save()
        response = self.login({'email': VALID_EMAIL, 'password': 'Pässwörd🔒123'})
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_very_long_values(self):
        """Overlong email and password return 400 without server error."""
        long_email = 'a' * 300 + '@example.com'
        self.assert_failed(self.login({'email': long_email, 'password': VALID_PASSWORD}))
        self.assert_failed(self.login({'email': VALID_EMAIL, 'password': 'x' * 5000}))

    def test_wrong_value_types(self):
        """Lists or objects instead of strings return 400."""
        for data in [{'email': [VALID_EMAIL], 'password': VALID_PASSWORD},
                     {'email': VALID_EMAIL, 'password': {'value': VALID_PASSWORD}}]:
            with self.subTest(data=data):
                self.assert_failed(self.login(data, format='json'))

    def test_sql_injection_like_email(self):
        """An injection attempt is just an invalid login."""
        self.assert_failed(self.login({'email': "' OR 1=1 --", 'password': "' OR 1=1 --"}))

    def test_user_with_unusable_password(self):
        """A user without a usable password cannot log in."""
        self.user.set_unusable_password()
        self.user.save()
        self.assert_failed(self.login())


class LoginRequestFormatTests(LoginTestMixin, APITestCase):
    """Malformed requests are rejected cleanly."""

    def test_malformed_json(self):
        """Broken JSON returns 400 and no cookies."""
        response = self.client.post(
            reverse('login'), '{"email": ', content_type='application/json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertNotIn('access_token', response.cookies)

    def test_unsupported_content_type(self):
        """Plain text bodies return 415."""
        response = self.client.post(reverse('login'), 'hello', content_type='text/plain')
        self.assertEqual(response.status_code, status.HTTP_415_UNSUPPORTED_MEDIA_TYPE)

    def test_rejected_methods_set_no_cookies(self):
        """A GET request never sets token cookies."""
        response = self.client.get(reverse('login'))
        self.assertNotIn('access_token', response.cookies)


@override_settings(CORS_ALLOWED_ORIGINS=['http://127.0.0.1:5501'])
class LoginCorsTests(LoginTestMixin, APITestCase):
    """The Live Server frontend may send and receive cookies."""

    def test_allowed_origin_gets_credentials_headers(self):
        """The frontend origin is allowed together with credentials."""
        response = self.login(HTTP_ORIGIN='http://127.0.0.1:5501')
        self.assertEqual(response['Access-Control-Allow-Origin'], 'http://127.0.0.1:5501')
        self.assertEqual(response['Access-Control-Allow-Credentials'], 'true')

    def test_foreign_origin_gets_no_cors_headers(self):
        """Other origins are not allowed to read the response."""
        response = self.login(HTTP_ORIGIN='http://evil.example.com')
        self.assertNotIn('Access-Control-Allow-Origin', response)


class LoginFlowTests(APITestCase):
    """Complete flow from registration over activation to login."""

    def register(self):
        """Register a new user through the endpoint and return the user."""
        data = {'email': FLOW_EMAIL, 'password': FLOW_PASSWORD,
                'confirmed_password': FLOW_PASSWORD}
        self.client.post(reverse('register'), data)
        return User.objects.get(email=FLOW_EMAIL)

    def login(self):
        """Log in with the registered credentials."""
        return self.client.post(reverse('login'), {'email': FLOW_EMAIL, 'password': FLOW_PASSWORD})

    def test_login_fails_before_and_works_after_activation(self):
        """Registered users can only log in after activating the account."""
        user = self.register()
        self.assertEqual(self.login().status_code, status.HTTP_400_BAD_REQUEST)
        token = default_token_generator.make_token(user)
        self.client.get(reverse('activate', args=[make_uidb64(user), token]))
        self.assertEqual(self.login().status_code, status.HTTP_200_OK)
