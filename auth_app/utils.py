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
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken

GENERIC_ERROR_MESSAGE = 'Please check your input and try again.'
ACTIVATION_SUCCESS_MESSAGE = 'Account successfully activated.'
ACTIVATION_FAILED_MESSAGE = 'Activation failed. The link is invalid or has expired.'
ACTIVATION_PATH = '/pages/auth/activate.html'
ACTIVATION_SUBJECT = 'Confirm your email'
PASSWORD_RESET_PATH = '/pages/auth/confirm_password.html'
PASSWORD_RESET_SUBJECT = 'Reset your Password'
PASSWORD_RESET_SENT_MESSAGE = 'An email has been sent to reset your password.'
LOGO_CID = 'videoflix-logo'
LOGO_PATH = Path(__file__).resolve().parent / 'assets' / 'logo.png'
LOGIN_SUCCESS_MESSAGE = 'Login successful'
ACCESS_COOKIE_NAME = 'access_token'
REFRESH_COOKIE_NAME = 'refresh_token'
LOGOUT_SUCCESS_MESSAGE = (
    'Logout successful! All tokens will be deleted. Refresh token is now invalid.'
)
REFRESH_SUCCESS_MESSAGE = 'Token refreshed'
REFRESH_MISSING_MESSAGE = 'Refresh token is missing.'
REFRESH_INVALID_MESSAGE = 'Refresh token is invalid or expired.'


def make_uidb64(user):
    """Return the user's primary key encoded as urlsafe base64."""
    return urlsafe_base64_encode(force_bytes(user.pk))


def make_user_token(user):
    """Return a one-time token for account activation and password reset."""
    return default_token_generator.make_token(user)


def build_registration_response(user):
    """Build the registration response body defined in the endpoint doc."""
    return {
        'user': {'id': user.id, 'email': user.email},
        'token': make_user_token(user),
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


def build_frontend_link(user, path):
    """Return a frontend URL with the user's uid and token as query."""
    query = urlencode({'uid': make_uidb64(user), 'token': make_user_token(user)})
    return f'{settings.FRONTEND_URL}{path}?{query}'


def build_activation_link(user):
    """Return the frontend activation URL with uid and token as query."""
    return build_frontend_link(user, ACTIVATION_PATH)


def build_password_reset_link(user):
    """Return the frontend password confirm URL with uid and token as query."""
    return build_frontend_link(user, PASSWORD_RESET_PATH)


def build_logo_part():
    """Return the Videoflix logo as inline PNG part (Gmail shows no SVG)."""
    logo = MIMEPart()
    logo.set_content(
        LOGO_PATH.read_bytes(), maintype='image', subtype='png',
        disposition='inline', filename='logo.png', cid=f'<{LOGO_CID}>',
    )
    return logo


def build_html_email(subject, template_name, context, recipient):
    """Build an HTML email with text fallback and inline logo from a template pair."""
    context = {**context, 'logo_cid': LOGO_CID}
    text_body = render_to_string(f'auth_app/emails/{template_name}.txt', context)
    html_body = render_to_string(f'auth_app/emails/{template_name}.html', context)
    email = EmailMultiAlternatives(subject, text_body, to=[recipient])
    email.attach_alternative(html_body, 'text/html')
    email.attach(build_logo_part())
    return email


def build_activation_email(user):
    """Build the activation email with the link to the frontend."""
    context = {'activation_link': build_activation_link(user)}
    return build_html_email(ACTIVATION_SUBJECT, 'activation_email', context, user.email)


def build_password_reset_email(user):
    """Build the password reset email with the link to the frontend."""
    context = {'reset_link': build_password_reset_link(user)}
    return build_html_email(PASSWORD_RESET_SUBJECT, 'password_reset_email', context, user.email)


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


def delete_token_cookies(response):
    """Delete the access and refresh token cookies in the browser."""
    for name in (ACCESS_COOKIE_NAME, REFRESH_COOKIE_NAME):
        response.delete_cookie(name, samesite='Lax')


def blacklist_refresh_token(raw_token):
    """Put the refresh token on the blacklist; return False if it is invalid."""
    try:
        RefreshToken(raw_token).blacklist()
    except TokenError:
        return False
    return True


def get_valid_refresh_token(raw_token):
    """Return the refresh token if it is valid and its user active, else None."""
    try:
        refresh_token = RefreshToken(raw_token)
    except TokenError:
        return None
    user_id = refresh_token.get('user_id')
    if not User.objects.filter(pk=user_id, is_active=True).exists():
        return None
    return refresh_token


def build_refresh_response(access_token):
    """Build the token refresh response body defined in the endpoint doc."""
    return {'detail': REFRESH_SUCCESS_MESSAGE, 'access': str(access_token)}


def find_active_user_by_email(email):
    """Return the active user with this email (case-insensitive) or None."""
    return User.objects.filter(email__iexact=email, is_active=True).first()
