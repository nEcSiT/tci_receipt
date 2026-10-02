from django.urls import path
from apps.receipts import views

app_name = "receipts"

urlpatterns = [
    path("", views.ReceiptListView.as_view(), name="receipt_list"),
    path("<uuid:receipt_id>/", views.ReceiptDetailView.as_view(), name="receipt_detail"),
    path("<uuid:receipt_id>/pdf/", views.ReceiptPdfView.as_view(), name="receipt_pdf"),
    path("<uuid:receipt_id>/edit/", views.ReceiptEditView.as_view(), name="receipt_edit"),
    path("<uuid:receipt_id>/delete/", views.ReceiptDeleteView.as_view(), name="receipt_delete"),
    path("generate/<uuid:contribution_id>/", views.ReceiptGenerateView.as_view(), name="receipt_generate"),
    path("verify/", views.ReceiptVerifyView.as_view(), name="receipt_verify"),
    path("verify/<str:receipt_number>/", views.ReceiptVerifyView.as_view(), name="receipt_verify_number"),
    path("access/<str:token>/", views.ReceiptAccessView.as_view(), name="receipt_access"),
]
