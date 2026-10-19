import django_rq
from django.contrib.auth.models import User

from auth_app.utils import build_activation_email, build_password_reset_email


def send_activation_email(user_id):
    """Send the activation email to the inactive user with the given id."""
    user = User.objects.filter(pk=user_id, is_active=False).first()
    if user is None:
        return
    build_activation_email(user).send()


def queue_activation_email(user):
    """Enqueue the activation email as background job for the RQ worker."""
    django_rq.enqueue(send_activation_email, user.pk)


def send_password_reset_email(user_id):
    """Send the password reset email to the active user with the given id."""
    user = User.objects.filter(pk=user_id, is_active=True).first()
    if user is None:
        return
    build_password_reset_email(user).send()


def queue_password_reset_email(user):
    """Enqueue the password reset email as background job for the RQ worker."""
    django_rq.enqueue(send_password_reset_email, user.pk)
