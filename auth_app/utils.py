from email.message import MIMEPart
from pathlib import Path
from urllib.parse import urlencode

from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.auth.tokens import default_token_generator
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from rest_framework_simplejwt.tokens import RefreshToken

GENERIC_ERROR_MESSAGE = 'Please check your input and try again.'
ACTIVATION_SUCCESS_MESSAGE = 'Account successfully activated.'
ACTIVATION_FAILED_MESSAGE = 'Activation failed. The link is invalid or has expired.'
ACTIVATION_PATH = '/pages/auth/activate.html'
ACTIVATION_SUBJECT = 'Confirm your email'
LOGO_CID = 'videoflix-logo'
LOGO_PATH = Path(__file__).resolve().parent / 'assets' / 'logo.png'
LOGIN_SUCCESS_MESSAGE = 'Login successful'
ACCESS_COOKIE_NAME = 'access_token'
REFRESH_COOKIE_NAME = 'refresh_token'


def make_uidb64(user):
    """Return the user's primary key encoded as urlsafe base64."""
    return urlsafe_base64_encode(force_bytes(user.pk))


def make_activation_token(user):
    """Return a one-time token used to activate the user's account."""
    return default_token_generator.make_token(user)


def build_registration_response(user):
    """Build the registration response body defined in the endpoint doc."""
    return {
        'user': {'id': user.id, 'email': user.email},
        'token': make_activation_token(user),
    }


def get_user_from_uidb64(uidb64):
    """Return the user encoded in uidb64, or None if it is invalid."""
    try:
        user_pk = force_str(urlsafe_base64_decode(uidb64))
        return User.objects.get(pk=user_pk)
    except (TypeError, ValueError, OverflowError, User.DoesNotExist):
        return None


def activate_user(uidb64, token):
    """Activate the inactive user if uid and token are valid; return success."""
    user = get_user_from_uidb64(uidb64)
    if user is None or user.is_active:
        return False
    if not default_token_generator.check_token(user, token):
        return False
    user.is_active = True
    user.save(update_fields=['is_active'])
    return True


def build_activation_link(user):
    """Return the frontend activation URL with uid and token as query."""
    query = urlencode({'uid': make_uidb64(user), 'token': make_activation_token(user)})
    return f'{settings.FRONTEND_URL}{ACTIVATION_PATH}?{query}'


def build_activation_context(user):
    """Return the template variables for the activation email."""
    return {
        'activation_link': build_activation_link(user),
        'logo_cid': LOGO_CID,
    }


def build_logo_part():
    """Return the Videoflix logo as inline PNG part (Gmail shows no SVG)."""
    logo = MIMEPart()
    logo.set_content(
        LOGO_PATH.read_bytes(), maintype='image', subtype='png',
        disposition='inline', filename='logo.png', cid=f'<{LOGO_CID}>',
    )
    return logo


def build_activation_email(user):
    """Build the HTML activation email with text fallback and inline logo."""
    context = build_activation_context(user)
    text_body = render_to_string('auth_app/emails/activation_email.txt', context)
    html_body = render_to_string('auth_app/emails/activation_email.html', context)
    email = EmailMultiAlternatives(ACTIVATION_SUBJECT, text_body, to=[user.email])
    email.attach_alternative(html_body, 'text/html')
    email.attach(build_logo_part())
    return email


def build_login_response(user):
    """Build the login response body defined in the endpoint doc."""
    return {
        'detail': LOGIN_SUCCESS_MESSAGE,
        'user': {'id': user.id, 'username': user.username},
    }


def set_token_cookie(response, name, token):
    """Set a JWT as HttpOnly cookie that expires together with the token."""
    response.set_cookie(
        name, str(token), max_age=int(token.lifetime.total_seconds()),
        httponly=True, samesite='Lax', secure=False,
    )


def set_login_cookies(response, user):
    """Create a token pair for the user and set both as cookies."""
    refresh_token = RefreshToken.for_user(user)
    set_token_cookie(response, ACCESS_COOKIE_NAME, refresh_token.access_token)
    set_token_cookie(response, REFRESH_COOKIE_NAME, refresh_token)
