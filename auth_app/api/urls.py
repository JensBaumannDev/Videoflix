from django.urls import path

from auth_app.api.views import RegistrationView

urlpatterns = [
    path('register/', RegistrationView.as_view(), name='register'),
]
