from datetime import datetime, timedelta
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.auth.tokens import PasswordResetTokenGenerator, default_token_generator
from django.core import mail
from django.test import TestCase, override_settings
from django.utils import timezone
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from auth_app.tasks import send_password_reset_email
from auth_app.utils import (
    GENERIC_ERROR_MESSAGE,
    LOGO_CID,
    build_password_reset_email,
    build_password_reset_link,
    make_uidb64,
)

VALID_EMAIL = 'user@example.com'
VALID_PASSWORD = 'securepassword'
FRONTEND_URL = 'http://frontend.test'
SUCCESS_BODY = {'detail': 'An email has been sent to reset your password.'}
ERROR_BODY = {'detail': GENERIC_ERROR_MESSAGE}


def create_user(email=VALID_EMAIL, is_active=True):
    """Create and return a user like the registration does."""
    return User.objects.create_user(
        username=email, email=email, password=VALID_PASSWORD, is_active=is_active)


class PasswordResetTestMixin:
    """Shared helpers for the password reset endpoint tests."""

    def setUp(self):
        """Create an active user and patch the RQ enqueue function."""
        self.user = create_user()
        enqueue_patcher = patch('django_rq.enqueue')
        self.enqueue = enqueue_patcher.start()
        self.addCleanup(enqueue_patcher.stop)

    def request_reset(self, data=None, **kwargs):
        """Send a POST request to the password reset endpoint."""
        payload = {'email': VALID_EMAIL} if data is None else data
        return self.client.post(reverse('password_reset'), payload, **kwargs)

    def assert_success_without_email(self, response):
        """Check for the success response and that no job was queued."""
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, SUCCESS_BODY)
        self.enqueue.assert_not_called()

    def assert_failed(self, response):
        """Check for 400 with the generic message and no queued job."""
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data, ERROR_BODY)
        self.enqueue.assert_not_called()


class PasswordResetSuccessTests(PasswordResetTestMixin, APITestCase):
    """An existing active user gets a reset email in the background."""

    def test_returns_200_with_doc_body(self):
        """The response body matches the endpoint doc exactly."""
        response = self.request_reset()
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, SUCCESS_BODY)

    def test_enqueues_reset_email_once(self):
        """The reset email task is queued once with the user's id."""
        self.request_reset()
        self.enqueue.assert_called_once_with(send_password_reset_email, self.user.pk)

    def test_sends_no_email_directly(self):
        """The view itself sends nothing, the RQ worker does."""
        self.request_reset()
        self.assertEqual(len(mail.outbox), 0)

    def test_email_is_case_insensitive(self):
        """Upper case input finds the user."""
        self.request_reset({'email': 'USER@Example.COM'})
        self.enqueue.assert_called_once_with(send_password_reset_email, self.user.pk)

    def test_whitespace_around_email_is_ignored(self):
        """Leading and trailing spaces are trimmed."""
        self.request_reset({'email': f'  {VALID_EMAIL} '})
        self.enqueue.assert_called_once_with(send_password_reset_email, self.user.pk)

    def test_json_request(self):
        """A JSON body like the frontend sends works."""
        response = self.request_reset({'email': VALID_EMAIL}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.enqueue.assert_called_once()

    def test_extra_fields_are_ignored(self):
        """Unknown fields change nothing."""
        response = self.request_reset({'email': VALID_EMAIL, 'is_staff': True})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.enqueue.assert_called_once()

    def test_repeated_requests_queue_one_email_each(self):
        """Every request queues a new email."""
        self.request_reset()
        self.request_reset()
        self.assertEqual(self.enqueue.call_count, 2)

    def test_user_whose_username_differs_from_email(self):
        """A user like the Docker superuser (username != email) is found by email."""
        admin = User.objects.create_superuser('admin', 'admin@example.com', 'adminpw')
        self.request_reset({'email': 'admin@example.com'})
        self.enqueue.assert_called_once_with(send_password_reset_email, admin.pk)

    def test_username_is_not_accepted_as_email(self):
        """Only the email is looked up, not the username."""
        User.objects.create_user('admin', 'admin@example.com', 'adminpw')
        self.assert_success_without_email(self.request_reset({'email': 'admin@example.org'}))

    def test_only_matching_user_gets_email(self):
        """Other users are not affected."""
        create_user('other@example.com')
        self.request_reset()
        self.enqueue.assert_called_once_with(send_password_reset_email, self.user.pk)

    def test_url_path_matches_endpoint_doc(self):
        """The route is exactly /api/password_reset/."""
        self.assertEqual(reverse('password_reset'), '/api/password_reset/')


class PasswordResetNoAccountLeakTests(PasswordResetTestMixin, APITestCase):
    """The response never reveals whether an account exists."""

    def test_unknown_email(self):
        """An unknown email gets the same 200 response, but no email."""
        self.assert_success_without_email(self.request_reset({'email': 'nobody@example.com'}))

    def test_inactive_user(self):
        """An inactive user gets the same response, but no email."""
        create_user('inactive@example.com', is_active=False)
        self.assert_success_without_email(self.request_reset({'email': 'inactive@example.com'}))

    def test_responses_are_identical(self):
        """Known, inactive and unknown emails produce byte-identical responses."""
        create_user('inactive@example.com', is_active=False)
        known = self.request_reset()
        for email in ['inactive@example.com', 'nobody@example.com']:
            with self.subTest(email=email):
                other = self.request_reset({'email': email})
                self.assertEqual(known.status_code, other.status_code)
                self.assertEqual(known.content, other.content)

    def test_responses_are_identical_for_invalid_access_cookie(self):
        """A logged-out visitor with old cookies gets the same answer."""
        self.client.cookies['access_token'] = 'garbage'
        response = self.request_reset({'email': 'nobody@example.com'})
        self.assert_success_without_email(response)

    def test_user_with_unusable_password(self):
        """A user without password (e.g. superuser setup) is handled like others."""
        self.user.set_unusable_password()
        self.user.save()
        response = self.request_reset()
        self.assertEqual(response.status_code, status.HTTP_200_OK)


class PasswordResetInvalidInputTests(PasswordResetTestMixin, APITestCase):
    """Invalid input returns 400 with the generic message."""

    def test_empty_body(self):
        """An empty body is rejected."""
        self.assert_failed(self.request_reset({}))

    def test_blank_email(self):
        """A blank or whitespace-only email is rejected."""
        for value in ['', '   ']:
            with self.subTest(value=value):
                self.assert_failed(self.request_reset({'email': value}))

    def test_null_email(self):
        """A null email is rejected."""
        self.assert_failed(self.request_reset({'email': None}, format='json'))

    def test_invalid_email_format(self):
        """Strings that are no email address are rejected."""
        for value in ['user', 'user@', '@example.com', 'user@@example.com', 'user example.com']:
            with self.subTest(value=value):
                self.assert_failed(self.request_reset({'email': value}))

    def test_wrong_value_types(self):
        """Lists, objects and numbers are rejected."""
        for value in [[VALID_EMAIL], {'email': VALID_EMAIL}, 123, True]:
            with self.subTest(value=value):
                self.assert_failed(self.request_reset({'email': value}, format='json'))

    def test_very_long_email(self):
        """An overlong email is rejected instead of crashing."""
        self.assert_failed(self.request_reset({'email': 'a' * 400 + '@example.com'}))

    def test_sql_injection_like_email(self):
        """SQL-like input is just an invalid email."""
        self.assert_failed(self.request_reset({'email': "user@example.com' OR '1'='1"}))

    def test_wrong_field_name(self):
        """Only the field 'email' is accepted."""
        self.assert_failed(self.request_reset({'username': VALID_EMAIL}))

    def test_malformed_json(self):
        """Broken JSON returns 400 and queues nothing."""
        response = self.client.post(
            reverse('password_reset'), '{"email": ', content_type='application/json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.enqueue.assert_not_called()


class PasswordResetAuthTests(PasswordResetTestMixin, APITestCase):
    """The endpoint needs no authentication."""

    def test_invalid_access_cookie_does_not_block_reset(self):
        """A broken access cookie must not cause a 401."""
        self.client.cookies['access_token'] = 'garbage'
        response = self.request_reset()
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.enqueue.assert_called_once()

    def test_other_methods_are_not_allowed(self):
        """GET, PUT, PATCH and DELETE return 405."""
        for method in ['get', 'put', 'patch', 'delete']:
            with self.subTest(method=method):
                response = getattr(self.client, method)(reverse('password_reset'))
                self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
        self.enqueue.assert_not_called()


@override_settings(FRONTEND_URL=FRONTEND_URL)
class PasswordResetLinkTests(TestCase):
    """The reset link points to the frontend with uid and token as query."""

    def setUp(self):
        """Create the user the link is built for."""
        self.user = create_user()
        self.link = urlsplit(build_password_reset_link(self.user))
        self.query = parse_qs(self.link.query)

    def test_link_points_to_frontend_confirm_page(self):
        """Scheme, host and path match FRONTEND_URL and the confirm page."""
        self.assertEqual(f'{self.link.scheme}://{self.link.netloc}', FRONTEND_URL)
        self.assertEqual(self.link.path, '/pages/auth/confirm_password.html')

    def test_link_contains_uid_of_user(self):
        """The uid query parameter is the user's uidb64."""
        self.assertEqual(self.query['uid'], [make_uidb64(self.user)])

    def test_link_contains_valid_token(self):
        """The token query parameter is a valid token for the user."""
        self.assertTrue(default_token_generator.check_token(self.user, self.query['token'][0]))

    def test_link_has_only_uid_and_token(self):
        """No other parameters are added to the link."""
        self.assertEqual(set(self.query), {'uid', 'token'})

    def test_token_is_invalid_after_password_change(self):
        """Once the password changed, the old link no longer works."""
        self.user.set_password('anotherpassword')
        self.user.save()
        self.assertFalse(default_token_generator.check_token(self.user, self.query['token'][0]))

    def check_token_at(self, moment):
        """Check the link token as if it were the given point in time."""
        with patch.object(PasswordResetTokenGenerator, '_now', return_value=moment):
            return default_token_generator.check_token(self.user, self.query['token'][0])

    def test_token_still_valid_shortly_before_24_hours(self):
        """The link works until the 24 hours are over."""
        self.assertTrue(self.check_token_at(datetime.now() + timedelta(hours=23, minutes=59)))

    def test_token_expires_after_24_hours(self):
        """After 24 hours the link no longer works."""
        self.assertFalse(self.check_token_at(datetime.now() + timedelta(hours=24, minutes=1)))

    def test_token_is_invalid_after_login(self):
        """Logging in after the request invalidates the link (Django default)."""
        self.user.last_login = timezone.now()
        self.user.save()
        self.assertFalse(default_token_generator.check_token(self.user, self.query['token'][0]))

    def test_token_of_other_user_is_invalid(self):
        """A token is bound to the user it was made for."""
        other = create_user('other@example.com')
        self.assertFalse(default_token_generator.check_token(other, self.query['token'][0]))

    def test_link_is_valid_for_24_hours(self):
        """The template promises 24 hours, the setting matches."""
        self.assertEqual(settings.PASSWORD_RESET_TIMEOUT, 24 * 60 * 60)


@override_settings(FRONTEND_URL=FRONTEND_URL, DEFAULT_FROM_EMAIL='noreply@videoflix.test')
class PasswordResetEmailContentTests(TestCase):
    """The reset email follows the design template of the frontend."""

    def setUp(self):
        """Build the reset email for an active user."""
        self.user = create_user()
        self.email = build_password_reset_email(self.user)
        self.html = self.email.alternatives[0].content

    def test_subject_matches_template(self):
        """The subject is 'Reset your Password'."""
        self.assertEqual(self.email.subject, 'Reset your Password')

    def test_is_sent_to_user_from_default_address(self):
        """Recipient is the user, sender is DEFAULT_FROM_EMAIL."""
        self.assertEqual(self.email.to, [VALID_EMAIL])
        self.assertEqual(self.email.from_email, 'noreply@videoflix.test')

    def test_has_html_alternative(self):
        """The email has exactly one HTML alternative."""
        self.assertEqual(len(self.email.alternatives), 1)
        self.assertEqual(self.email.alternatives[0].mimetype, 'text/html')

    def test_html_contains_template_texts(self):
        """Greeting, button, hints and closing of the template are present."""
        for text in ['Hello,', 'request to reset your password', 'Reset password',
                     'only valid for 24 hours', 'please ignore this email',
                     'Best regards,', 'Your Videoflix team!']:
            with self.subTest(text=text):
                self.assertIn(text, self.html)

    def test_html_button_links_to_confirm_page(self):
        """The button href contains the frontend confirm page and uid."""
        self.assertIn(f'href="{FRONTEND_URL}/pages/auth/confirm_password.html?uid=', self.html)
        self.assertIn(make_uidb64(self.user), self.html)

    def test_logo_is_below_the_text(self):
        """Unlike the activation email, the logo sits at the bottom."""
        self.assertGreater(self.html.index(f'cid:{LOGO_CID}'), self.html.index('Best regards,'))

    def test_html_is_responsive(self):
        """The template sets a viewport for mobile clients."""
        self.assertIn('name="viewport"', self.html)

    def test_plain_text_fallback_contains_link(self):
        """The plain text body contains greeting, link and validity hint."""
        self.assertIn('Hello,', self.email.body)
        self.assertIn(f'{FRONTEND_URL}/pages/auth/confirm_password.html?uid=', self.email.body)
        self.assertIn('only valid for 24 hours', self.email.body)

    def test_plain_text_link_is_not_html_escaped(self):
        """The '&' in the text link stays a plain '&'."""
        self.assertIn('&token=', self.email.body)
        self.assertNotIn('&amp;', self.email.body)


class PasswordResetEmailLogoTests(TestCase):
    """The logo is embedded as inline PNG because Gmail shows no SVG."""

    def setUp(self):
        """Build the reset email and get the logo attachment."""
        self.email = build_password_reset_email(create_user())
        self.logo = self.email.attachments[0]

    def test_logo_is_single_inline_png(self):
        """Exactly one inline PNG attachment with the logo content id."""
        self.assertEqual(len(self.email.attachments), 1)
        self.assertEqual(self.logo.get_content_type(), 'image/png')
        self.assertEqual(self.logo.get_content_disposition(), 'inline')
        self.assertEqual(self.logo['Content-ID'], f'<{LOGO_CID}>')

    def test_html_contains_no_svg(self):
        """No SVG is used in the email."""
        self.assertNotIn('svg', self.email.alternatives[0].content.lower())


class SendPasswordResetEmailTaskTests(TestCase):
    """The RQ task sends the reset email to an active user."""

    def test_task_sends_one_email_to_user(self):
        """The task puts exactly one email into the outbox."""
        send_password_reset_email(create_user().pk)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [VALID_EMAIL])
        self.assertEqual(mail.outbox[0].subject, 'Reset your Password')

    def test_task_ignores_unknown_user(self):
        """A deleted or unknown user id sends nothing and does not crash."""
        send_password_reset_email(999999)
        self.assertEqual(len(mail.outbox), 0)

    def test_task_ignores_inactive_user(self):
        """A user deactivated in the meantime gets no email."""
        send_password_reset_email(create_user(is_active=False).pk)
        self.assertEqual(len(mail.outbox), 0)

    def test_sent_email_is_multipart_with_html(self):
        """The real message contains the HTML part and the logo."""
        send_password_reset_email(create_user().pk)
        message = mail.outbox[0].message().as_string()
        self.assertIn('text/html', message)
        self.assertIn('image/png', message)
