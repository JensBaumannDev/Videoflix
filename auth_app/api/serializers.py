from django.contrib.auth import authenticate
from django.contrib.auth.models import User
from django.db.models import Q
from rest_framework import serializers


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
        if any(char.isspace() for char in value):
            raise serializers.ValidationError('Password must not contain spaces.')
        return value

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
