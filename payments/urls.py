from django.urls import path

from . import views

urlpatterns = [
    path("mpesa/<str:token>/confirmation/", views.mpesa_confirmation, name="mpesa-confirmation"),
    path("mpesa/<str:token>/validation/", views.mpesa_validation, name="mpesa-validation"),
]
