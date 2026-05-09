from django.urls import path

from . import views

urlpatterns = [
    path("settings/tariff/", views.tariff_settings, name="tariff_settings"),
]
