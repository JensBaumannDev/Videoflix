from django.urls import path

from auth_app.api.views import ActivationView, LoginView, RegistrationView

urlpatterns = [
    path('register/', RegistrationView.as_view(), name='register'),
    path('activate/<str:uidb64>/<str:token>/', ActivationView.as_view(), name='activate'),
    path('login/', LoginView.as_view(), name='login'),
]
