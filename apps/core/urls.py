from django.urls import path
from apps.core.views import health_check, index_view, DashboardView

from apps.reports.views import PaymentSummaryView

app_name = "core"

urlpatterns = [
    path("", index_view, name="index"),
    path("dashboard/", DashboardView.as_view(), name="dashboard"),
    path("dashboard/payment-methods/", PaymentSummaryView.as_view(), name="payment_summary"),
    path("health/", health_check, name="health_check"),
]
