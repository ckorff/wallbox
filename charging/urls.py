from django.urls import path

from . import views

urlpatterns = [
    path("dashboard/", views.dashboard, name="dashboard"),
    path("settings/tariff/", views.tariff_settings, name="tariff_settings"),
    path("reports/", views.reports_index, name="reports_index"),
]
