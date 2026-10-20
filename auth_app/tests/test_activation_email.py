from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.auth.tokens import default_token_generator
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APITestCase

from auth_app.tasks import send_activation_email
from auth_app.utils import LOGO_CID, build_activation_email, build_activation_link, make_uidb64

VALID_EMAIL = 'john.doe@example.com'
FRONTEND_URL = 'http://frontend.test'


def create_inactive_user(email=VALID_EMAIL):
    """Create and return an inactive user like the registration does."""
    return User.objects.create_user(username=email, email=email, password='x', is_active=False)


@override_settings(FRONTEND_URL=FRONTEND_URL)
class ActivationLinkTests(TestCase):
    """The activation link points to the frontend with uid and token as query."""

    def setUp(self):
        """Create the user the link is built for."""
        self.user = create_inactive_user()
        self.link = urlsplit(build_activation_link(self.user))

    def test_link_points_to_frontend_activation_page(self):
        """Scheme, host and path match FRONTEND_URL and the activate page."""
        self.assertEqual(f'{self.link.scheme}://{self.link.netloc}', FRONTEND_URL)
        self.assertEqual(self.link.path, '/pages/auth/activate.html')

    def test_link_contains_uid_of_user(self):
        """The uid query parameter is the user's uidb64."""
        self.assertEqual(parse_qs(self.link.query)['uid'], [make_uidb64(self.user)])

    def test_link_contains_valid_token(self):
        """The token query parameter is a valid activation token."""
        token = parse_qs(self.link.query)['token'][0]
        self.assertTrue(default_token_generator.check_token(self.user, token))

    def test_link_has_only_uid_and_token(self):
        """No other parameters are added to the link."""
        self.assertEqual(set(parse_qs(self.link.query).keys()), {'uid', 'token'})


@override_settings(FRONTEND_URL=FRONTEND_URL, DEFAULT_FROM_EMAIL='noreply@videoflix.test')
class ActivationEmailContentTests(TestCase):
    """The activation email follows the design template of the frontend."""

    def setUp(self):
        """Build the activation email for a new user."""
        self.user = create_inactive_user()
        self.email = build_activation_email(self.user)
        self.html = self.email.alternatives[0].content

    def test_subject_matches_template(self):
        """The subject is 'Confirm your email'."""
        self.assertEqual(self.email.subject, 'Confirm your email')

    def test_is_sent_to_user_from_default_address(self):
        """Recipient is the user, sender is DEFAULT_FROM_EMAIL."""
        self.assertEqual(self.email.to, [VALID_EMAIL])
        self.assertEqual(self.email.from_email, 'noreply@videoflix.test')

    def test_has_html_alternative(self):
        """The email has exactly one HTML alternative."""
        self.assertEqual(len(self.email.alternatives), 1)
        self.assertEqual(self.email.alternatives[0].mimetype, 'text/html')

    def test_html_contains_template_texts(self):
        """Greeting, button, hint and closing of the template are present."""
        for text in ['Dear Videoflix user,', 'Activate account', 'please disregard this email',
                     'Best regards,', 'Your Videoflix Team.']:
            with self.subTest(text=text):
                self.assertIn(text, self.html)

    def test_html_button_links_to_activation_page(self):
        """The button href contains the frontend activation page and uid."""
        self.assertIn(f'href="{FRONTEND_URL}/pages/auth/activate.html?uid=', self.html)
        self.assertIn(make_uidb64(self.user), self.html)

    def test_html_is_responsive(self):
        """The template sets a viewport for mobile clients."""
        self.assertIn('name="viewport"', self.html)

    def test_plain_text_fallback_contains_link(self):
        """The plain text body contains greeting and activation link."""
        self.assertIn('Dear Videoflix user,', self.email.body)
        self.assertIn(f'{FRONTEND_URL}/pages/auth/activate.html?uid=', self.email.body)


class ActivationEmailLogoTests(TestCase):
    """The logo is embedded as PNG because Gmail does not show SVG."""

    def setUp(self):
        """Build the activation email and get the logo attachment."""
        self.email = build_activation_email(create_inactive_user())
        self.logo = self.email.attachments[0]

    def test_logo_is_single_png_attachment(self):
        """Exactly one attachment with content type image/png."""
        self.assertEqual(len(self.email.attachments), 1)
        self.assertEqual(self.logo.get_content_type(), 'image/png')

    def test_logo_is_inline_with_content_id(self):
        """The logo is inline and referenced in the HTML by its cid."""
        self.assertEqual(self.logo.get_content_disposition(), 'inline')
        self.assertEqual(self.logo['Content-ID'], f'<{LOGO_CID}>')
        self.assertIn(f'src="cid:{LOGO_CID}"', self.email.alternatives[0].content)

    def test_logo_content_is_png_file(self):
        """The attached bytes start with the PNG signature."""
        self.assertTrue(self.logo.get_content().startswith(b'\x89PNG'))

    def test_html_contains_no_svg(self):
        """No SVG is used in the email."""
        self.assertNotIn('svg', self.email.alternatives[0].content.lower())


class SendActivationEmailTaskTests(TestCase):
    """The RQ task sends the email for an inactive user."""

    def test_task_sends_one_email_to_user(self):
        """The task puts exactly one email into the outbox."""
        user = create_inactive_user()
        send_activation_email(user.pk)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [VALID_EMAIL])

    def test_task_ignores_unknown_user(self):
        """A deleted or unknown user id sends nothing and does not crash."""
        send_activation_email(999999)
        self.assertEqual(len(mail.outbox), 0)

    def test_task_ignores_active_user(self):
        """An already active user gets no activation email."""
        user = create_inactive_user()
        User.objects.filter(pk=user.pk).update(is_active=True)
        send_activation_email(user.pk)
        self.assertEqual(len(mail.outbox), 0)

    def test_sent_email_is_multipart_with_html(self):
        """The real message contains the HTML part and the logo."""
        send_activation_email(create_inactive_user().pk)
        message = mail.outbox[0].message().as_string()
        self.assertIn('text/html', message)
        self.assertIn('image/png', message)


class RegistrationQueuesActivationEmailTests(APITestCase):
    """The registration enqueues the activation email as RQ job."""

    url = reverse('register')
    payload = {'email': VALID_EMAIL, 'password': 'securepw', 'confirmed_password': 'securepw'}

    def test_successful_registration_enqueues_task_once(self):
        """One job with the new user's id is enqueued."""
        with patch('django_rq.enqueue') as enqueue:
            self.client.post(self.url, self.payload, format='json')
        user = User.objects.get(email=VALID_EMAIL)
        enqueue.assert_called_once_with(
            send_activation_email, user.pk, settings.FRONTEND_URL)

    def test_failed_registration_enqueues_nothing(self):
        """Invalid input does not enqueue any job."""
        with patch('django_rq.enqueue') as enqueue:
            self.client.post(self.url, {**self.payload, 'confirmed_password': 'x'}, format='json')
        enqueue.assert_not_called()

    def test_registration_sends_no_email_directly(self):
        """The request itself sends no email, the worker does."""
        with patch('django_rq.enqueue'):
            self.client.post(self.url, self.payload, format='json')
        self.assertEqual(len(mail.outbox), 0)
