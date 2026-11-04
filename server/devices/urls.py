from django.urls import path
from . import views

urlpatterns = [
    path("devices/", views.devices, name="devices"),
    path("devices/<slug:device_id>/", views.device_detail, name="device_detail"),
    path("devices/<slug:device_id>/readings/", views.readings, name="readings"),
    path("devices/<slug:device_id>/actions/", views.actions, name="actions"),
    path("overview/", views.api_overview, name="overview"),
]
