from django.urls import path

from . import views

urlpatterns = [
    path("dashboard/", views.dashboard, name="dashboard"),
    path("settings/", views.settings_page, name="settings_page"),
    path("reports/", views.reports_index, name="reports_index"),
]
