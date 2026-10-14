from django.contrib.auth.tokens import default_token_generator
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

GENERIC_ERROR_MESSAGE = 'Please check your input and try again.'


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
