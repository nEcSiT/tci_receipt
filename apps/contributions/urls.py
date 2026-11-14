from django.urls import path
from apps.contributions import views

app_name = "contributions"

urlpatterns = [
    path("", views.ContributionListView.as_view(), name="contribution_list"),
    path("create/", views.ContributionCreateView.as_view(), name="contribution_create"),
    path("bulk/", views.BulkContributionEntryView.as_view(), name="bulk_entry"),
    path("bulk/upload/", views.BulkContributionUploadView.as_view(), name="bulk_upload"),
    path("bulk/template/", views.BulkContributionTemplateView.as_view(), name="bulk_template"),
    path("bulk/confirm/", views.BulkContributionConfirmView.as_view(), name="bulk_confirm"),
    path("welfare/", views.WelfareOverviewView.as_view(), name="welfare_overview"),
    path("members/search/", views.MemberSearchApiView.as_view(), name="member_search"),
    path("<uuid:contribution_id>/", views.ContributionDetailView.as_view(), name="contribution_detail"),
    path("webhook/payment/", views.PaymentWebhookView.as_view(), name="payment_webhook"),
    path("ussd/", views.UssdGatewayView.as_view(), name="ussd_gateway"),
]
