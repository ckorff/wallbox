"""
URL configuration for wallbox_project project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
import re

from django.conf import settings
from django.contrib import admin
from django.urls import include, path, re_path
from django.views.generic import RedirectView
from django.views.static import serve as static_serve

urlpatterns = [
    path('admin/', admin.site.urls),
    path(
        '',
        RedirectView.as_view(pattern_name='dashboard', permanent=False),
    ),
    path('', include('charging.urls')),
]

# Single-user LAN-only deployment: serve /media/ via Django's static view
# regardless of DEBUG so report PDFs work under Gunicorn. The
# django.conf.urls.static.static() helper is a no-op when DEBUG=False, so
# we register the route directly.
_media_prefix = re.escape(settings.MEDIA_URL.lstrip("/"))
urlpatterns += [
    re_path(
        rf"^{_media_prefix}(?P<path>.*)$",
        static_serve,
        kwargs={"document_root": settings.MEDIA_ROOT},
    ),
]
