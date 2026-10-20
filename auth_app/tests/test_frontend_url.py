from unittest.mock import patch

from django.contrib.auth.models import User
from django.core import mail
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APITestCase

from auth_app.tasks import send_activation_email, send_password_reset_email
from auth_app.utils import build_activation_link, build_password_reset_link, get_frontend_url

FRONTEND_URL = 'http://frontend.test'
ALLOWED_ORIGINS = ['http://127.0.0.1:5500', 'http://127.0.0.1:5501', 'http://localhost:5500']
REGISTER_DATA = {'email': 'new@example.com', 'password': 'securepw',
                 'confirmed_password': 'securepw'}


@override_settings(FRONTEND_URL=FRONTEND_URL, CORS_ALLOWED_ORIGINS=ALLOWED_ORIGINS)
class GetFrontendUrlTests(TestCase):
    """The email links point to the frontend the request came from."""

    def frontend_url_for(self, **headers):
        """Return get_frontend_url for a request with the given headers."""
        return get_frontend_url(RequestFactory().post('/api/register/', **headers))

    def test_allowed_origin_is_used(self):
        """A frontend on port 5500 gets links to port 5500."""
        url = self.frontend_url_for(HTTP_ORIGIN='http://127.0.0.1:5500')
        self.assertEqual(url, 'http://127.0.0.1:5500')

    def test_every_allowed_origin_works(self):
        """Ports and hosts from CORS_ALLOWED_ORIGINS are all accepted."""
        for origin in ALLOWED_ORIGINS:
            with self.subTest(origin=origin):
                self.assertEqual(self.frontend_url_for(HTTP_ORIGIN=origin), origin)

    def test_missing_origin_falls_back_to_setting(self):
        """Requests without Origin (e.g. from tests or curl) use FRONTEND_URL."""
        self.assertEqual(self.frontend_url_for(), FRONTEND_URL)

    def test_unknown_origin_is_never_used(self):
        """A foreign origin cannot inject phishing links into the emails."""
        for origin in ['https://evil.example', 'http://127.0.0.1:8000', 'null', '']:
            with self.subTest(origin=origin):
                self.assertEqual(self.frontend_url_for(HTTP_ORIGIN=origin), FRONTEND_URL)

    def test_origin_must_match_exactly(self):
        """Look-alike origins with other scheme or suffix are rejected."""
        for origin in ['https://127.0.0.1:5500', 'http://127.0.0.1:5500.evil.example',
                       'http://127.0.0.1:5500/']:
            with self.subTest(origin=origin):
                self.assertEqual(self.frontend_url_for(HTTP_ORIGIN=origin), FRONTEND_URL)


@override_settings(FRONTEND_URL=FRONTEND_URL)
class FrontendLinkTests(TestCase):
    """Link builders accept the frontend URL of the request."""

    def setUp(self):
        """Create a user to build links for."""
        self.user = User.objects.create_user('u@example.com', 'u@example.com', 'pw')

    def test_activation_link_uses_given_frontend(self):
        """The given base URL replaces FRONTEND_URL."""
        link = build_activation_link(self.user, 'http://127.0.0.1:5500')
        self.assertTrue(link.startswith('http://127.0.0.1:5500/pages/auth/activate.html?uid='))

    def test_reset_link_uses_given_frontend(self):
        """The reset link uses the given base URL as well."""
        link = build_password_reset_link(self.user, 'http://127.0.0.1:5500')
        expected = 'http://127.0.0.1:5500/pages/auth/confirm_password.html?uid='
        self.assertTrue(link.startswith(expected))

    def test_links_fall_back_to_setting(self):
        """Without a base URL the FRONTEND_URL setting is used."""
        self.assertTrue(build_activation_link(self.user).startswith(FRONTEND_URL))
        self.assertTrue(build_password_reset_link(self.user).startswith(FRONTEND_URL))


@override_settings(FRONTEND_URL=FRONTEND_URL, CORS_ALLOWED_ORIGINS=ALLOWED_ORIGINS)
class EmailLinkEndToEndTests(APITestCase):
    """From the request to the sent email the frontend origin is kept."""

    def run_queued_job(self, enqueue):
        """Run the job that the view put into the (mocked) queue."""
        task, *arguments = enqueue.call_args.args
        task(*arguments)
        return mail.outbox[-1].body

    def test_activation_email_links_to_request_origin(self):
        """Registering from port 5500 sends a link to port 5500."""
        with patch('django_rq.enqueue') as enqueue:
            self.client.post(reverse('register'), REGISTER_DATA,
                             HTTP_ORIGIN='http://127.0.0.1:5500')
        body = self.run_queued_job(enqueue)
        self.assertIn('http://127.0.0.1:5500/pages/auth/activate.html?uid=', body)

    def test_reset_email_links_to_request_origin(self):
        """Requesting a reset from port 5500 sends a link to port 5500."""
        User.objects.create_user('u@example.com', 'u@example.com', 'pw')
        with patch('django_rq.enqueue') as enqueue:
            self.client.post(reverse('password_reset'), {'email': 'u@example.com'},
                             HTTP_ORIGIN='http://127.0.0.1:5500')
        body = self.run_queued_job(enqueue)
        self.assertIn('http://127.0.0.1:5500/pages/auth/confirm_password.html?uid=', body)

    def test_foreign_origin_gets_default_link(self):
        """A forged Origin header falls back to FRONTEND_URL."""
        with patch('django_rq.enqueue') as enqueue:
            self.client.post(reverse('register'), REGISTER_DATA,
                             HTTP_ORIGIN='https://evil.example')
        body = self.run_queued_job(enqueue)
        self.assertNotIn('evil.example', body)
        self.assertIn(f'{FRONTEND_URL}/pages/auth/activate.html?uid=', body)

    def test_jobs_without_frontend_url_still_work(self):
        """Jobs queued before this change (only user id) still send emails."""
        user = User.objects.create_user('u@example.com', 'u@example.com', 'pw')
        send_password_reset_email(user.pk)
        inactive = User.objects.create_user('i@example.com', 'i@example.com', 'pw',
                                            is_active=False)
        send_activation_email(inactive.pk)
        self.assertEqual(len(mail.outbox), 2)
