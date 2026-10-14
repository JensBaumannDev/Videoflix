from django.contrib.auth.models import User
from django.db.models import Q
from rest_framework import serializers


class RegistrationSerializer(serializers.Serializer):
    """Validate registration data and create an inactive user."""

    email = serializers.EmailField(max_length=150)
    password = serializers.CharField(write_only=True)
    confirmed_password = serializers.CharField(write_only=True)

    def validate_email(self, value):
        """Normalize the email and reject addresses that are already registered."""
        email = value.lower()
        if User.objects.filter(Q(email__iexact=email) | Q(username__iexact=email)).exists():
            raise serializers.ValidationError('Email is already registered.')
        return email

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
