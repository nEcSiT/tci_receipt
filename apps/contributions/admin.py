from django.contrib import admin
from .models import ContributionType, PaymentTransaction, Contribution


@admin.register(ContributionType)
class ContributionTypeAdmin(admin.ModelAdmin):
    list_display = ("name", "is_active", "created_at")


@admin.register(PaymentTransaction)
class PaymentTransactionAdmin(admin.ModelAdmin):
    list_display = ("provider", "provider_reference", "transaction_phone", "amount", "currency", "status", "created_at")
    search_fields = ("provider_reference", "transaction_phone")
    list_filter = ("provider", "status")


@admin.register(Contribution)
class ContributionAdmin(admin.ModelAdmin):
    list_display = ("contribution_number", "contribution_date", "amount", "currency", "contribution_type", "payment_mode", "entry_method", "status", "created_at")
    search_fields = ("contribution_number", "reference_number")
    list_filter = ("entry_method", "payment_mode", "status", "contribution_type", "contribution_date")
