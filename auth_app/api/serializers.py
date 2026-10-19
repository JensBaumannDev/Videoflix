from django.contrib.auth import authenticate
from django.contrib.auth.models import User
from django.db.models import Q
from rest_framework import serializers


def reject_whitespace(password):
    """Raise a validation error if the password contains any whitespace."""
    if any(char.isspace() for char in password):
        raise serializers.ValidationError('Password must not contain spaces.')
    return password


class RegistrationSerializer(serializers.Serializer):
    """Validate registration data and create an inactive user."""

    email = serializers.EmailField(max_length=150)
    password = serializers.CharField(write_only=True, trim_whitespace=False)
    confirmed_password = serializers.CharField(write_only=True, trim_whitespace=False)

    def validate_email(self, value):
        """Normalize the email and reject addresses that are already registered."""
        email = value.lower()
        if User.objects.filter(Q(email__iexact=email) | Q(username__iexact=email)).exists():
            raise serializers.ValidationError('Email is already registered.')
        return email

    def validate_password(self, value):
        """Reject passwords that contain any whitespace character."""
        return reject_whitespace(value)

    def validate(self, attrs):
        """Ensure both password fields match."""
        if attrs['password'] != attrs['confirmed_password']:
            raise serializers.ValidationError('Passwords do not match.')
        return attrs

    def create(self, validated_data):
        """Create the user with username = email and is_active = False."""
        return User.objects.create_user(
            username=validated_data['email'],
            email=validated_data['email'],
            password=validated_data['password'],
            is_active=False,
        )


class LoginSerializer(serializers.Serializer):
    """Validate email and password of an active user."""

    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, trim_whitespace=False)

    def validate(self, attrs):
        """Authenticate the user; inactive users and wrong data fail alike."""
        user = authenticate(username=attrs['email'].lower(), password=attrs['password'])
        if user is None:
            raise serializers.ValidationError('Invalid credentials.')
        attrs['user'] = user
        return attrs


class PasswordResetSerializer(serializers.Serializer):
    """Validate the email address for a password reset request."""

    email = serializers.EmailField()


class PasswordConfirmSerializer(serializers.Serializer):
    """Validate the new password and its confirmation for a password reset."""

    new_password = serializers.CharField(write_only=True, trim_whitespace=False)
    confirm_password = serializers.CharField(write_only=True, trim_whitespace=False)

    def validate_new_password(self, value):
        """Reject passwords that contain any whitespace character."""
        return reject_whitespace(value)

    def validate(self, attrs):
        """Ensure both password fields match."""
        if attrs['new_password'] != attrs['confirm_password']:
            raise serializers.ValidationError('Passwords do not match.')
        return attrs
