"""Project-level URL routing.

Root path redirects to the charging-app dashboard; ``/admin/`` keeps the
Django admin under the "Raw data" nav label. ``/media/`` is served by
Django's static-file view *even when DEBUG=False* — this is a LAN-only
single-user deployment behind no reverse proxy, so report PDFs need to
be reachable directly from the Gunicorn process.
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
