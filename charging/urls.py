"""URL routing for the charging app — three staff-only pages."""
from django.urls import path

from . import views

urlpatterns = [
    path("dashboard/", views.dashboard, name="dashboard"),
    path("settings/", views.settings_page, name="settings_page"),
    path(
        "settings/tariff-document/create/",
        views.tariff_document_create,
        name="tariff_document_create",
    ),
    path(
        "settings/tariff-document/<int:pk>/delete/",
        views.tariff_document_delete,
        name="tariff_document_delete",
    ),
    path("reports/", views.reports_index, name="reports_index"),
]
