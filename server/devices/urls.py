from django.urls import path
from . import views

urlpatterns = [
    path("devices/", views.devices, name="devices"),
    path("devices/<slug:device_id>/", views.device_detail, name="device_detail"),
    path("devices/<slug:device_id>/readings/", views.readings, name="readings"),
    path("devices/<slug:device_id>/actions/", views.actions, name="actions"),
    path("devices/<slug:device_id>/files/", views.file_upload, name="file_upload"),
    path("devices/<slug:device_id>/files/list/", views.file_list, name="file_list"),
    path("files/<int:pk>/", views.file_download, name="file_download"),
    path("overview/", views.api_overview, name="overview"),
]
