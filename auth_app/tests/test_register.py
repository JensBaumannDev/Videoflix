from django.contrib.auth.models import User
from django.contrib.auth.tokens import default_token_generator
from django.urls import reverse
from django.utils.encoding import force_str
from django.utils.http import urlsafe_base64_decode
from rest_framework import status
from rest_framework.test import APITestCase

from auth_app.utils import GENERIC_ERROR_MESSAGE, make_uidb64

VALID_EMAIL = 'user@example.com'
VALID_PASSWORD = 'securepassword'


class RegisterTestMixin:
    """Shared helpers for the registration endpoint tests."""

    url = reverse('register')

    def build_payload(self, **overrides):
        """Return a valid registration payload with optional overrides."""
        payload = {
            'email': VALID_EMAIL,
            'password': VALID_PASSWORD,
            'confirmed_password': VALID_PASSWORD,
        }
        payload.update(overrides)
        return payload

    def register(self, **overrides):
        """Send a registration request and return the response."""
        return self.client.post(self.url, self.build_payload(**overrides), format='json')


class RegisterSuccessTests(RegisterTestMixin, APITestCase):
    """A valid registration creates an inactive user."""

    def test_returns_201(self):
        """Valid data returns 201 Created."""
        self.assertEqual(self.register().status_code, status.HTTP_201_CREATED)

    def test_response_matches_endpoint_doc(self):
        """The response contains exactly user (id, email) and token."""
        response = self.register()
        user = User.objects.get(email=VALID_EMAIL)
        self.assertEqual(set(response.data.keys()), {'user', 'token'})
        self.assertEqual(response.data['user'], {'id': user.id, 'email': VALID_EMAIL})

    def test_response_contains_no_password(self):
        """Neither password field is echoed back."""
        body = str(self.register().data)
        self.assertNotIn(VALID_PASSWORD, body)

    def test_user_is_created_inactive(self):
        """The new user stays inactive until activation."""
        self.register()
        self.assertFalse(User.objects.get(email=VALID_EMAIL).is_active)

    def test_username_equals_email(self):
        """The username is set to the email address."""
        self.register()
        self.assertEqual(User.objects.get(email=VALID_EMAIL).username, VALID_EMAIL)

    def test_password_is_hashed(self):
        """The password is stored hashed and can be checked."""
        self.register()
        user = User.objects.get(email=VALID_EMAIL)
        self.assertNotEqual(user.password, VALID_PASSWORD)
        self.assertTrue(user.check_password(VALID_PASSWORD))

    def test_token_is_valid_activation_token(self):
        """The returned token belongs to the new user."""
        token = self.register().data['token']
        user = User.objects.get(email=VALID_EMAIL)
        self.assertTrue(default_token_generator.check_token(user, token))

    def test_email_is_stored_lowercase(self):
        """Emails are normalized to lowercase."""
        response = self.register(email='User@Example.COM')
        self.assertEqual(response.data['user']['email'], VALID_EMAIL)
        self.assertTrue(User.objects.filter(username=VALID_EMAIL).exists())

    def test_user_is_not_staff(self):
        """A registered user gets no admin rights."""
        self.register()
        user = User.objects.get(email=VALID_EMAIL)
        self.assertFalse(user.is_staff or user.is_superuser)


class RegisterErrorTests(RegisterTestMixin, APITestCase):
    """Invalid data returns 400 with a generic message and creates no user."""

    def assert_rejected(self, response):
        """Check for 400, the generic message and that no user exists."""
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data, {'detail': GENERIC_ERROR_MESSAGE})
        self.assertFalse(User.objects.filter(email=VALID_EMAIL).exists())

    def test_passwords_do_not_match(self):
        """Different passwords are rejected."""
        self.assert_rejected(self.register(confirmed_password='otherpassword'))

    def test_missing_fields(self):
        """Each required field is checked."""
        for field in ['email', 'password', 'confirmed_password']:
            with self.subTest(field=field):
                payload = self.build_payload()
                payload.pop(field)
                self.assert_rejected(self.client.post(self.url, payload, format='json'))

    def test_empty_fields(self):
        """Empty strings are rejected."""
        for field in ['email', 'password', 'confirmed_password']:
            with self.subTest(field=field):
                self.assert_rejected(self.register(**{field: ''}))

    def test_invalid_email(self):
        """Malformed email addresses are rejected."""
        for email in ['no-at-sign', 'user@', '@example.com', 'user example@x.com']:
            with self.subTest(email=email):
                self.assert_rejected(self.register(email=email))

    def test_empty_body(self):
        """A request without body is rejected."""
        self.assert_rejected(self.client.post(self.url, {}, format='json'))


class RegisterDuplicateTests(RegisterTestMixin, APITestCase):
    """An already registered email cannot register again."""

    def setUp(self):
        """Create an existing user with the test email."""
        User.objects.create_user(username=VALID_EMAIL, email=VALID_EMAIL, password='x')

    def assert_duplicate_rejected(self, response):
        """Check for 400, the generic message and that no second user exists."""
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data, {'detail': GENERIC_ERROR_MESSAGE})
        self.assertEqual(User.objects.count(), 1)

    def test_existing_email_is_rejected(self):
        """The same email returns 400."""
        self.assert_duplicate_rejected(self.register())

    def test_existing_email_other_case_is_rejected(self):
        """The duplicate check ignores upper and lower case."""
        self.assert_duplicate_rejected(self.register(email='USER@example.com'))

    def test_existing_username_is_rejected(self):
        """An email that is already used as username (e.g. admin) is rejected."""
        User.objects.filter(email=VALID_EMAIL).update(email='other@example.com')
        self.assert_duplicate_rejected(self.register())

    def test_duplicate_error_does_not_reveal_existing_account(self):
        """The message is the same as for any other invalid input."""
        duplicate = self.register().data
        mismatch = self.register(email='new@example.com', confirmed_password='x').data
        self.assertEqual(duplicate, mismatch)


class RegisterMethodTests(RegisterTestMixin, APITestCase):
    """Only POST is allowed on the registration endpoint."""

    def test_other_methods_are_not_allowed(self):
        """GET, PUT, PATCH and DELETE return 405."""
        for method in ['get', 'put', 'patch', 'delete']:
            with self.subTest(method=method):
                response = getattr(self.client, method)(self.url)
                self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

    def test_url_path_matches_endpoint_doc(self):
        """The endpoint lives at /api/register/."""
        self.assertEqual(self.url, '/api/register/')


def build_email(length):
    """Return a valid email address with exactly the given length."""
    domain_length = length - len('user@.com')
    labels = ['a' * 50] * (domain_length // 51)
    labels.append('a' * (domain_length - len(labels) * 51))
    return f"user@{'.'.join(labels)}.com"


class RegisterBoundaryTests(RegisterTestMixin, APITestCase):
    """Length limits, whitespace and special characters."""

    def test_email_with_150_chars_is_accepted(self):
        """150 chars is the username limit, so it is still allowed."""
        email = build_email(150)
        self.assertEqual(self.register(email=email).status_code, status.HTTP_201_CREATED)
        self.assertTrue(User.objects.filter(username=email).exists())

    def test_email_with_151_chars_is_rejected(self):
        """Longer emails would not fit into the username field."""
        response = self.register(email=build_email(151))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(User.objects.count(), 0)

    def test_email_surrounding_whitespace_is_stripped(self):
        """Leading and trailing spaces around the email are removed."""
        response = self.register(email=f'  {VALID_EMAIL}  ')
        self.assertEqual(response.data['user']['email'], VALID_EMAIL)

    def test_whitespace_only_values_are_rejected(self):
        """Fields containing only spaces count as empty."""
        for field in ['email', 'password', 'confirmed_password']:
            with self.subTest(field=field):
                response = self.register(**{field: '   '})
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(User.objects.count(), 0)

    def test_password_is_case_sensitive(self):
        """Passwords differing only in case do not match."""
        response = self.register(confirmed_password=VALID_PASSWORD.upper())
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_unicode_password_is_accepted(self):
        """Umlauts and emoji in passwords work and can be checked."""
        password = 'pässwörd-🔒-ß'
        self.register(password=password, confirmed_password=password)
        self.assertTrue(User.objects.get(email=VALID_EMAIL).check_password(password))

    def test_long_password_is_accepted(self):
        """Long passwords are stored without truncation."""
        password = 'p' * 500
        self.register(password=password, confirmed_password=password)
        self.assertTrue(User.objects.get(email=VALID_EMAIL).check_password(password))

    def test_email_with_plus_and_subdomain_is_accepted(self):
        """Valid special formats like plus addressing are allowed."""
        email = 'first.last+tag@mail.example.co.uk'
        self.assertEqual(self.register(email=email).status_code, status.HTTP_201_CREATED)


class RegisterInvalidInputTests(RegisterTestMixin, APITestCase):
    """Wrong types, null values and malformed request bodies."""

    def assert_bad_request(self, response):
        """Check for 400 and that no user was created."""
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(User.objects.count(), 0)

    def test_null_values_are_rejected(self):
        """null is not accepted for any field."""
        for field in ['email', 'password', 'confirmed_password']:
            with self.subTest(field=field):
                self.assert_bad_request(self.register(**{field: None}))

    def test_wrong_types_are_rejected(self):
        """Lists and objects are not valid field values."""
        for value in [['a@b.com'], {'email': 'a@b.com'}]:
            with self.subTest(value=value):
                self.assert_bad_request(self.register(email=value))
                self.assert_bad_request(self.register(password=value, confirmed_password=value))

    def test_malformed_json_is_rejected(self):
        """A body that is not valid JSON returns 400."""
        response = self.client.post(self.url, '{"email": ', content_type='application/json')
        self.assert_bad_request(response)

    def test_json_list_body_is_rejected(self):
        """A JSON array instead of an object returns 400."""
        self.assert_bad_request(self.client.post(self.url, [self.build_payload()], format='json'))


class RegisterSecurityTests(RegisterTestMixin, APITestCase):
    """Clients cannot grant themselves extra rights or skip activation."""

    def test_extra_fields_cannot_activate_or_promote_user(self):
        """is_active, is_staff and is_superuser in the body are ignored."""
        self.register(is_active=True, is_staff=True, is_superuser=True)
        user = User.objects.get(email=VALID_EMAIL)
        self.assertFalse(user.is_active or user.is_staff or user.is_superuser)

    def test_username_in_body_is_ignored(self):
        """The username is always the email, never a client value."""
        self.register(username='hacker')
        self.assertFalse(User.objects.filter(username='hacker').exists())

    def test_id_in_body_is_ignored(self):
        """A client cannot choose the primary key."""
        response = self.register(id=999)
        self.assertNotEqual(response.data['user']['id'], 999)

    def test_form_encoded_request_is_accepted(self):
        """Form data works as well as JSON."""
        response = self.client.post(self.url, self.build_payload())
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_response_is_json(self):
        """The response uses the JSON content type."""
        self.assertEqual(self.register()['Content-Type'], 'application/json')

    def test_options_request_is_allowed(self):
        """CORS preflight (OPTIONS) is answered with 200."""
        self.assertEqual(self.client.options(self.url).status_code, status.HTTP_200_OK)


class RegisterMultipleUsersTests(RegisterTestMixin, APITestCase):
    """Several registrations in a row behave independently."""

    def test_two_users_get_different_ids_and_tokens(self):
        """Each user receives an own id and activation token."""
        first = self.register().data
        second = self.register(email='second@example.com').data
        self.assertNotEqual(first['user']['id'], second['user']['id'])
        self.assertNotEqual(first['token'], second['token'])

    def test_registration_works_after_failed_attempt(self):
        """A rejected request does not block a later valid one."""
        self.register(confirmed_password='wrong')
        self.assertEqual(self.register().status_code, status.HTTP_201_CREATED)

    def test_inactive_existing_user_cannot_register_again(self):
        """A not yet activated account still blocks its email."""
        self.register()
        response = self.register()
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(User.objects.count(), 1)

    def test_token_of_other_user_is_invalid(self):
        """An activation token only works for its own user."""
        token = self.register().data['token']
        self.register(email='second@example.com')
        other_user = User.objects.get(email='second@example.com')
        self.assertFalse(default_token_generator.check_token(other_user, token))


class MakeUidb64Tests(APITestCase):
    """The uidb64 helper encodes the user's primary key."""

    def test_uidb64_decodes_to_user_pk(self):
        """Decoding the uidb64 returns the user's pk."""
        user = User.objects.create_user(username=VALID_EMAIL, email=VALID_EMAIL, password='x')
        decoded_pk = force_str(urlsafe_base64_decode(make_uidb64(user)))
        self.assertEqual(decoded_pk, str(user.pk))
