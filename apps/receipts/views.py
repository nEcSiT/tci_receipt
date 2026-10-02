import datetime
from decimal import Decimal, InvalidOperation
from django.contrib import messages
from django.core.exceptions import ValidationError, PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Sum, Q, Count
from django.http import HttpResponse, Http404
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.views import View

from apps.accounts.models import Role
from apps.audit.models import AuditLog
from apps.contributions.models import Contribution, ContributionType, PaymentMode
from apps.core.services.storage import get_storage_service
from apps.receipts.models import Receipt, ReceiptStatus, ReceiptEdit, ReceiptDeliveryRecord
from apps.receipts.services import ReceiptService, ReceiptPdfService


def can_view_receipt(user, receipt: Receipt | None = None) -> bool:
    """Verifies whether user has permission to view the receipt."""
    if not user.is_authenticated or not user.is_active:
        return False
    if user.is_system_administrator:
        return True
    if receipt:
        if receipt.generated_by_user == user and user.has_permission("receipt.view_own"):
            return True
        if user.has_permission("contribution.view"):
            return True
        return False
    return user.has_permission("receipt.view_own") or user.has_permission("contribution.view")


def can_edit_receipt(user, receipt: Receipt) -> bool:
    """Verifies whether user has permission to edit the manual receipt."""
    if not user.is_authenticated or not user.is_active:
        return False
    if receipt.generated_by_system or receipt.status == ReceiptStatus.DELETED:
        return False
    if user.is_system_administrator:
        return True
    return receipt.generated_by_user == user and user.has_permission("receipt.edit_own")


def can_delete_receipt(user, receipt: Receipt) -> bool:
    """Verifies whether user has permission to soft-delete the manual receipt."""
    if not user.is_authenticated or not user.is_active:
        return False
    if receipt.generated_by_system or receipt.status == ReceiptStatus.DELETED:
        return False
    if user.is_system_administrator:
        return True
    return receipt.generated_by_user == user and user.has_permission("receipt.delete_own")


def can_generate_receipt(user) -> bool:
    """Verifies whether user has permission to generate a receipt."""
    if not user.is_authenticated or not user.is_active:
        return False
    return (
        user.is_system_administrator
        or user.has_permission("contribution.create")
        or user.has_permission("receipt.edit_own")
    )


class ReceiptListView(View):
    """
    Searchable, filterable, and paginated directory of receipts.
    Enforces role-based visibility:
    - System Administrator can inspect all receipts (including soft-deleted).
    - System Users with receipt.view_own see only their own generated receipts.
    - System Users with contribution.view see all active receipts.
    """

    def get(self, request):
        if not can_view_receipt(request.user):
            if not request.user.is_authenticated:
                return redirect(f"/login/?next={request.path}")
            raise PermissionDenied("You do not have permission to view receipts.")

        # Base QuerySet
        is_admin = request.user.is_system_administrator
        if is_admin:
            qs = Receipt.all_objects.all()
        elif request.user.has_permission("contribution.view"):
            qs = Receipt.objects.all()
        else:
            qs = Receipt.objects.filter(generated_by_user=request.user)

        qs = qs.select_related(
            "contribution__member",
            "contribution__temporary_contributor",
            "contribution__contribution_type",
            "generated_by_user",
            "deleted_by",
        )

        # Filters
        query = request.GET.get("q", "").strip()
        status_filter = request.GET.get("status", "ACTIVE" if not is_admin else "all").strip()
        type_filter = request.GET.get("type", "").strip()
        mode_filter = request.GET.get("mode", "").strip()
        source_filter = request.GET.get("source", "").strip()
        date_from = request.GET.get("date_from", "").strip()
        date_to = request.GET.get("date_to", "").strip()

        if query:
            qs = qs.filter(
                Q(receipt_number__icontains=query)
                | Q(contribution__contribution_number__icontains=query)
                | Q(contribution__member__first_name__icontains=query)
                | Q(contribution__member__last_name__icontains=query)
                | Q(contribution__member__member_number__icontains=query)
                | Q(contribution__temporary_contributor__provider_name__icontains=query)
                | Q(contribution__temporary_contributor__first_name__icontains=query)
                | Q(contribution__temporary_contributor__last_name__icontains=query)
            )

        if is_admin:
            if status_filter == "ACTIVE":
                qs = qs.filter(status=ReceiptStatus.ACTIVE)
            elif status_filter == "DELETED":
                qs = qs.filter(status=ReceiptStatus.DELETED)
        else:
            qs = qs.filter(status=ReceiptStatus.ACTIVE)

        if type_filter:
            qs = qs.filter(contribution__contribution_type_id=type_filter)

        if mode_filter:
            qs = qs.filter(contribution__payment_mode=mode_filter)

        if source_filter == "auto":
            qs = qs.filter(generated_by_system=True)
        elif source_filter == "manual":
            qs = qs.filter(generated_by_system=False)

        if date_from:
            try:
                d_from = datetime.date.fromisoformat(date_from)
                qs = qs.filter(generated_at__date__gte=d_from)
            except ValueError:
                pass

        if date_to:
            try:
                d_to = datetime.date.fromisoformat(date_to)
                qs = qs.filter(generated_at__date__lte=d_to)
            except ValueError:
                pass

        # KPIs based on user's authorized scope
        kpi_qs = qs.filter(status=ReceiptStatus.ACTIVE)
        total_count = kpi_qs.count()
        total_amount = kpi_qs.aggregate(total=Sum("contribution__amount"))["total"] or Decimal("0.00")
        auto_count = kpi_qs.filter(generated_by_system=True).count()
        manual_count = kpi_qs.filter(generated_by_system=False).count()
        deleted_count = qs.filter(status=ReceiptStatus.DELETED).count() if is_admin else 0

        # Pagination
        paginator = Paginator(qs, 25)
        page_number = request.GET.get("page", 1)
        page_obj = paginator.get_page(page_number)

        contribution_types = ContributionType.objects.filter(is_active=True)

        return render(
            request,
            "receipts/receipt_list.html",
            {
                "receipts": page_obj,
                "page_obj": page_obj,
                "is_paginated": page_obj.has_other_pages(),
                "query": query,
                "status_filter": status_filter,
                "type_filter": type_filter,
                "mode_filter": mode_filter,
                "source_filter": source_filter,
                "date_from": date_from,
                "date_to": date_to,
                "total_count": total_count,
                "total_amount": total_amount,
                "auto_count": auto_count,
                "manual_count": manual_count,
                "deleted_count": deleted_count,
                "contribution_types": contribution_types,
                "payment_modes": PaymentMode.choices,
                "is_system_administrator": is_admin,
            },
        )


class ReceiptDetailView(View):
    """
    Detailed inspection of an official Receipt:
    - Contributor name (phone numbers strictly omitted)
    - Full contribution financial specs
    - Generation method (HLC/System vs User)
    - Status & Deletion audit (if soft-deleted)
    - Full field-level modification history (ReceiptEdit)
    - SMS Delivery logs (ReceiptDeliveryRecord)
    - System audit logs
    """

    def get(self, request, receipt_id):
        is_admin = request.user.is_authenticated and request.user.is_system_administrator

        manager = Receipt.all_objects if is_admin else Receipt.objects
        receipt = get_object_or_404(
            manager.select_related(
                "contribution__member",
                "contribution__temporary_contributor",
                "contribution__contribution_type",
                "contribution__payment_transaction",
                "generated_by_user",
                "deleted_by",
            ),
            id=receipt_id,
        )

        if not can_view_receipt(request.user, receipt):
            if not request.user.is_authenticated:
                return redirect(f"/login/?next={request.path}")
            raise PermissionDenied("You do not have permission to view this receipt.")

        edits = receipt.edits.select_related("user").order_by("-created_at")
        deliveries = receipt.deliveries.all().order_by("-created_at")
        audit_logs = AuditLog.objects.filter(
            entity_type="Receipt", entity_id=receipt.id
        ).order_by("-created_at")

        can_edit = can_edit_receipt(request.user, receipt)
        can_delete = can_delete_receipt(request.user, receipt)

        return render(
            request,
            "receipts/receipt_detail.html",
            {
                "receipt": receipt,
                "contribution": receipt.contribution,
                "edits": edits,
                "deliveries": deliveries,
                "audit_logs": audit_logs,
                "can_edit": can_edit,
                "can_delete": can_delete,
                "is_system_administrator": is_admin,
            },
        )


class ReceiptPdfView(View):
    """
    Serves the compiled PDF document for a receipt with inline Content-Disposition.
    If stored PDF is missing, transparently generates and caches it.
    """

    def get(self, request, receipt_id):
        is_admin = request.user.is_authenticated and request.user.is_system_administrator
        manager = Receipt.all_objects if is_admin else Receipt.objects
        receipt = get_object_or_404(manager, id=receipt_id)

        if not can_view_receipt(request.user, receipt):
            if not request.user.is_authenticated:
                return redirect(f"/login/?next={request.path}")
            raise PermissionDenied("You do not have permission to access this receipt PDF.")

        storage = get_storage_service()
        pdf_bytes = None

        if receipt.pdf_storage_key and storage.exists(receipt.pdf_storage_key):
            try:
                pdf_bytes = storage.get(receipt.pdf_storage_key)
            except Exception:
                pdf_bytes = None

        if not pdf_bytes:
            try:
                pdf_bytes = ReceiptPdfService.render_pdf(receipt)
                ReceiptPdfService.generate_and_store_pdf(receipt)
            except Exception as e:
                return HttpResponse(f"Error compiling receipt PDF: {e}", status=500)

        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = f'inline; filename="{receipt.receipt_number}.pdf"'
        return response


class ReceiptGenerateView(View):
    """
    Generates an official receipt for a Contribution if not already generated.
    """

    def post(self, request, contribution_id):
        if not can_generate_receipt(request.user):
            if not request.user.is_authenticated:
                return redirect(f"/login/?next={request.path}")
            raise PermissionDenied("You do not have permission to generate receipts.")

        contribution = get_object_or_404(Contribution, id=contribution_id)

        try:
            receipt = ReceiptService.generate_receipt(
                contribution=contribution,
                generated_by_user=request.user,
                actor=request.user,
            )
            messages.success(
                request,
                f"Official Receipt {receipt.receipt_number} generated successfully.",
            )
            return redirect("receipts:receipt_detail", receipt_id=receipt.id)
        except ValidationError as e:
            messages.error(request, str(e))
            return redirect("contributions:contribution_detail", contribution_id=contribution.id)


class ReceiptEditView(View):
    """
    Edits a manual receipt:
    - Creator-only (or System Administrator)
    - Requires receipt.edit_own
    - Automatic receipts are strictly blocked
    - Mandatory reason required
    - Logs field-level changes to ReceiptEdit and AuditLog
    - Regenerates PDF
    """

    def get(self, request, receipt_id):
        receipt = get_object_or_404(Receipt.objects.select_related("contribution"), id=receipt_id)

        if not can_edit_receipt(request.user, receipt):
            if not request.user.is_authenticated:
                return redirect(f"/login/?next={request.path}")
            raise PermissionDenied("You do not have permission to edit this receipt.")

        contribution_types = ContributionType.objects.filter(is_active=True)
        return render(
            request,
            "receipts/receipt_edit.html",
            {
                "receipt": receipt,
                "contribution": receipt.contribution,
                "contribution_types": contribution_types,
                "payment_modes": PaymentMode.choices,
            },
        )

    def post(self, request, receipt_id):
        receipt = get_object_or_404(Receipt.objects.select_related("contribution"), id=receipt_id)

        if not can_edit_receipt(request.user, receipt):
            if not request.user.is_authenticated:
                return redirect(f"/login/?next={request.path}")
            raise PermissionDenied("You do not have permission to edit this receipt.")

        amount_str = request.POST.get("amount", "").strip()
        type_id = request.POST.get("contribution_type_id", "").strip()
        payment_mode = request.POST.get("payment_mode", "").strip()
        ref_num = request.POST.get("reference_number", "").strip()
        custom_desc = request.POST.get("custom_type_description", "").strip()
        contrib_date = request.POST.get("contribution_date", "").strip()
        reason = request.POST.get("reason", "").strip()

        changed_fields = {}
        if amount_str:
            changed_fields["amount"] = amount_str
        if type_id:
            changed_fields["contribution_type_id"] = type_id
        if payment_mode:
            changed_fields["payment_mode"] = payment_mode
        if "reference_number" in request.POST:
            changed_fields["reference_number"] = ref_num
        if "custom_type_description" in request.POST:
            changed_fields["custom_type_description"] = custom_desc
        if contrib_date:
            changed_fields["contribution_date"] = contrib_date

        try:
            ReceiptService.edit_manual_receipt(
                receipt=receipt,
                user=request.user,
                changed_fields=changed_fields,
                reason=reason,
            )
            messages.success(
                request,
                f"Receipt {receipt.receipt_number} updated successfully and PDF regenerated.",
            )
            return redirect("receipts:receipt_detail", receipt_id=receipt.id)
        except ValidationError as e:
            messages.error(request, str(e.message if hasattr(e, "message") else e))
            contribution_types = ContributionType.objects.filter(is_active=True)
            return render(
                request,
                "receipts/receipt_edit.html",
                {
                    "receipt": receipt,
                    "contribution": receipt.contribution,
                    "contribution_types": contribution_types,
                    "payment_modes": PaymentMode.choices,
                    "form_data": request.POST,
                    "error": str(e),
                },
                status=400,
            )


class ReceiptDeleteView(View):
    """
    Soft-deletes a manual receipt:
    - Creator-only (or System Administrator)
    - Requires receipt.delete_own
    - Automatic receipts are strictly immutable and cannot be deleted
    """

    def post(self, request, receipt_id):
        receipt = get_object_or_404(Receipt.objects, id=receipt_id)

        if not can_delete_receipt(request.user, receipt):
            if not request.user.is_authenticated:
                return redirect(f"/login/?next={request.path}")
            raise PermissionDenied("You do not have permission to delete this receipt.")

        reason = request.POST.get("reason", "").strip()
        try:
            ReceiptService.delete_manual_receipt(
                receipt=receipt,
                user=request.user,
                reason=reason,
            )
            messages.success(
                request,
                f"Manual receipt {receipt.receipt_number} has been soft-deleted.",
            )
            return redirect("receipts:receipt_list")
        except ValidationError as e:
            messages.error(request, str(e))
            return redirect("receipts:receipt_detail", receipt_id=receipt.id)


class ReceiptVerifyView(View):
    """
    Public and administrative receipt verification portal:
    - Enter receipt number (e.g., HLC-5831047)
    - Verifies active, soft-deleted, or non-existent status
    - Contributor phone numbers are NEVER returned or displayed
    """

    def get(self, request, receipt_number=None):
        num = (receipt_number or request.GET.get("receipt_number", "")).strip()
        result = None
        if num:
            result = ReceiptService.verify_receipt(num)

        return render(
            request,
            "receipts/receipt_verify.html",
            {
                "receipt_number": num,
                "result": result,
            },
        )


class ReceiptAccessView(View):
    """
    Public secure receipt access link via signed token (dispatched via SMS).
    Allows recipient to view and download their official PDF without authentication.
    """

    def get(self, request, token):
        receipt = ReceiptService.get_receipt_by_access_token(token)
        if not receipt:
            return render(
                request,
                "receipts/receipt_access.html",
                {
                    "is_valid": False,
                    "error_message": "This receipt access link is invalid, expired, or has been revoked.",
                },
                status=404,
            )

        if receipt.status == ReceiptStatus.DELETED:
            return render(
                request,
                "receipts/receipt_access.html",
                {
                    "is_valid": False,
                    "error_message": "This receipt has been revoked / soft-deleted and is no longer accessible.",
                },
                status=410,
            )

        # If download query param requested, stream the PDF
        if request.GET.get("download") == "1":
            storage = get_storage_service()
            try:
                pdf_bytes = storage.get(receipt.pdf_storage_key)
            except Exception:
                pdf_bytes = ReceiptPdfService.render_pdf(receipt)
            response = HttpResponse(pdf_bytes, content_type="application/pdf")
            response["Content-Disposition"] = f'attachment; filename="{receipt.receipt_number}.pdf"'
            return response

        return render(
            request,
            "receipts/receipt_access.html",
            {
                "is_valid": True,
                "receipt": receipt,
                "token": token,
            },
        )
