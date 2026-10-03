from django.contrib import admin
from django.urls import path

from routing.views import home, health, route_fuel_plan


urlpatterns = [
    path("", home, name="home"),

    path("api/health/", health, name="health"),
    path("api/route/", route_fuel_plan, name="route"),
]