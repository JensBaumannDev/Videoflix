from django.urls import path

from auth_app.api.views import (
    ActivationView,
    CookieTokenRefreshView,
    LoginView,
    LogoutView,
    PasswordConfirmView,
    PasswordResetView,
    RegistrationView,
)

urlpatterns = [
    path('register/', RegistrationView.as_view(), name='register'),
    path('activate/<str:uidb64>/<str:token>/', ActivationView.as_view(), name='activate'),
    path('login/', LoginView.as_view(), name='login'),
    path('logout/', LogoutView.as_view(), name='logout'),
    path('token/refresh/', CookieTokenRefreshView.as_view(), name='token_refresh'),
    path('password_reset/', PasswordResetView.as_view(), name='password_reset'),
    path(
        'password_confirm/<str:uidb64>/<str:token>/',
        PasswordConfirmView.as_view(), name='password_confirm',
    ),
]
