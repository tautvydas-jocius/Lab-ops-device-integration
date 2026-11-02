from django.urls import path
from . import views
urlpatterns = [path("devices/", views.devices), path("devices/<slug:device_id>/", views.device_detail), path("devices/<slug:device_id>/readings/", views.readings)]
