from django.contrib import admin
from .models import Receipt, ReceiptEdit, ReceiptDeliveryRecord


@admin.register(Receipt)
class ReceiptAdmin(admin.ModelAdmin):
    list_display = ("receipt_number", "contribution", "generated_by_system", "generated_by_user", "status", "generated_at")
    search_fields = ("receipt_number", "contribution__contribution_number")
    list_filter = ("generated_by_system", "status")

    def get_queryset(self, request):
        return Receipt.all_objects.all()


@admin.register(ReceiptEdit)
class ReceiptEditAdmin(admin.ModelAdmin):
    list_display = ("receipt", "field_name", "user", "created_at")
    search_fields = ("receipt__receipt_number", "field_name")


@admin.register(ReceiptDeliveryRecord)
class ReceiptDeliveryRecordAdmin(admin.ModelAdmin):
    list_display = ("receipt", "recipient_phone", "channel", "status", "attempt_count", "delivered_at")
    search_fields = ("receipt__receipt_number", "recipient_phone")
    list_filter = ("channel", "status")
