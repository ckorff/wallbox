from django.urls import path

from . import views

urlpatterns = [
    path("settings/tariff/", views.tariff_settings, name="tariff_settings"),
    path("house-usage/", views.house_usage, name="house_usage"),
    path("reports/", views.reports_index, name="reports_index"),
]
