"""
URL configuration for loanguard_ai project.
"""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from app.views import ingest_pipeline_view, login_view, logout_view
from app.views.dashboard import dashboard_view

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", dashboard_view, name="dashboard"),
    path("login/", login_view, name="login"),
    path("logout/", logout_view, name="logout"),
    path("ingest/pipeline/", ingest_pipeline_view, name="ingest_pipeline"),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    if "debug_toolbar" in settings.INSTALLED_APPS:
        urlpatterns += [path("__debug__/", include("debug_toolbar.urls"))]
    if "django_browser_reload" in settings.INSTALLED_APPS:
        urlpatterns += [path("__reload__/", include("django_browser_reload.urls"))]
