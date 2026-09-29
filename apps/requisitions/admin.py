from django.contrib import admin
from .models import (
    Requisition,
    RequisitionAttachment,
    RequisitionAssignment,
    RequisitionApproval,
    RequisitionDisbursement,
    RequisitionEvidence,
)


@admin.register(Requisition)
class RequisitionAdmin(admin.ModelAdmin):
    list_display = ("requisition_number", "requester_name", "amount_requested", "status", "created_at")
    search_fields = ("requisition_number", "requester_name", "requester_phone", "purpose")
    list_filter = ("status", "preferred_disbursement_method")


@admin.register(RequisitionAttachment)
class RequisitionAttachmentAdmin(admin.ModelAdmin):
    list_display = ("file_name", "requisition", "uploader_name", "file_size", "created_at")


@admin.register(RequisitionAssignment)
class RequisitionAssignmentAdmin(admin.ModelAdmin):
    list_display = ("requisition", "assigned_to", "assigned_at", "is_current")
    list_filter = ("is_current",)


@admin.register(RequisitionApproval)
class RequisitionApprovalAdmin(admin.ModelAdmin):
    list_display = ("requisition", "approver", "decision", "approved_amount", "created_at")
    list_filter = ("decision",)


@admin.register(RequisitionDisbursement)
class RequisitionDisbursementAdmin(admin.ModelAdmin):
    list_display = ("requisition", "amount", "method", "recipient_name", "disbursed_at", "processed_by")
    list_filter = ("method",)


@admin.register(RequisitionEvidence)
class RequisitionEvidenceAdmin(admin.ModelAdmin):
    list_display = ("file_name", "requisition", "uploader_name", "status", "created_at")
    list_filter = ("status",)
