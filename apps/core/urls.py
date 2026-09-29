from django.urls import path
from apps.core.views import health_check, index_view, DashboardView

app_name = "core"

urlpatterns = [
    path("", index_view, name="index"),
    path("dashboard/", DashboardView.as_view(), name="dashboard"),
    path("health/", health_check, name="health_check"),
]
