from django.urls import path
from apps.reports.views import PaymentSummaryView

app_name = "reports"

urlpatterns = [
    path("", PaymentSummaryView.as_view(), name="payment_summary"),
    path("payment-methods/", PaymentSummaryView.as_view(), name="payment_methods"),
]
