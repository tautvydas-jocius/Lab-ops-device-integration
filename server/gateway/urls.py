from django.contrib import admin
from django.urls import include, path, re_path
from django.views.static import serve
from django.conf import settings
from devices import views as device_views

urlpatterns = [
    path("", device_views.dashboard, name="dashboard"),
    path("api/overview/", device_views.api_overview, name="overview_root"),
    path("admin/", admin.site.urls),
    path("api/", include("devices.urls")),
    re_path(r"^static/(?P<path>.*)$", serve, {"document_root": settings.STATIC_ROOT}),
]
