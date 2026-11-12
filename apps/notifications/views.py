import json
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from apps.audit.models import AuditResult
from apps.audit.services import AuditService
from apps.contributions.models import ContributionType
from apps.notifications.forms import NotificationTemplateForm
from apps.notifications.models import (
    Notification,
    NotificationStatus,
    NotificationTemplate,
    NotificationType,
)
from apps.notifications.services import NotificationTemplateService


def check_template_manage_permission(user) -> bool:
    """Verifies that the user is the System Administrator or explicitly authorized to manage SMS templates."""
    if not user.is_authenticated or not user.is_active:
        return False
    return (
        getattr(user, "is_system_administrator", False)
        or user.has_permission("template.manage")
        or user.has_permission("notification.manage")
    )


class NotificationListView(LoginRequiredMixin, View):
    """
    TCI-HLC Design System Section 19:
    Display: notification type, recipient, channel, status, attempts, last attempt,
    provider reference, failure reason where applicable.
    """

    def get(self, request):
        if not request.user.is_active or not (
            request.user.is_system_administrator
            or request.user.has_permission("notification.view")
        ):
            raise PermissionDenied("You do not have permission to view notification history.")
        qs = Notification.objects.order_by("-created_at")

        query = request.GET.get("q", "").strip()
        if query:
            qs = qs.filter(
                Q(recipient_phone__icontains=query)
                | Q(message__icontains=query)
                | Q(provider_reference__icontains=query)
            )

        ntype = request.GET.get("type")
        if ntype in NotificationType.values:
            qs = qs.filter(notification_type=ntype)

        status = request.GET.get("status")
        if status in NotificationStatus.values:
            qs = qs.filter(status=status)

        paginator = Paginator(qs, 25)
        page_number = request.GET.get("page")
        page_obj = paginator.get_page(page_number)

        return render(
            request,
            "notifications/list.html",
            {
                "page": page_obj,
                "notification_types": NotificationType.choices,
                "notification_statuses": NotificationStatus.choices,
                "can_manage_templates": check_template_manage_permission(request.user),
            },
        )


class NotificationDetailView(LoginRequiredMixin, View):
    """
    Detailed operational view of an outbound notification message.
    Renders the exact saved message without regeneration or side effects.
    Preserves authorization checks for notification history and related records.
    """

    def get(self, request, notification_id):
        if not request.user.is_active or not (
            request.user.is_system_administrator
            or request.user.has_permission("notification.view")
        ):
            raise PermissionDenied("You do not have permission to view notification details.")

        notification = get_object_or_404(
            Notification.objects.select_related("template").prefetch_related("attempts"),
            id=notification_id,
        )

        attempts = notification.attempts.all().order_by("-attempted_at")

        # Related record resolution and permission checks
        related_contribution = None
        can_view_contribution = False
        related_receipt = None
        can_view_receipt_record = False
        related_requisition = None
        can_view_requisition = False

        if notification.related_record_type == "Contribution" and notification.related_record_id:
            from apps.contributions.models import Contribution
            from apps.receipts.models import Receipt
            from apps.receipts.views import can_view_receipt

            related_contribution = Contribution.objects.filter(id=notification.related_record_id).first()
            if related_contribution:
                can_view_contribution = (
                    request.user.is_system_administrator
                    or request.user.has_permission("contribution.view")
                )
                related_receipt = Receipt.all_objects.filter(contribution=related_contribution).first()
                if related_receipt:
                    can_view_receipt_record = can_view_receipt(request.user, related_receipt)

        elif notification.related_record_type == "Receipt" and notification.related_record_id:
            from apps.receipts.models import Receipt
            from apps.receipts.views import can_view_receipt

            related_receipt = Receipt.all_objects.filter(id=notification.related_record_id).first()
            if related_receipt:
                can_view_receipt_record = can_view_receipt(request.user, related_receipt)
                related_contribution = related_receipt.contribution
                if related_contribution:
                    can_view_contribution = (
                        request.user.is_system_administrator
                        or request.user.has_permission("contribution.view")
                    )

        elif notification.related_record_type == "Requisition" and notification.related_record_id:
            from apps.requisitions.models import Requisition
            related_requisition = Requisition.objects.filter(id=notification.related_record_id).first()
            if related_requisition:
                can_view_requisition = (
                    request.user.is_system_administrator
                    or request.user.has_permission("requisition.view")
                )

        return_query = request.GET.urlencode()

        return render(
            request,
            "notifications/detail.html",
            {
                "notification": notification,
                "attempts": attempts,
                "related_contribution": related_contribution,
                "can_view_contribution": can_view_contribution,
                "related_receipt": related_receipt,
                "can_view_receipt": can_view_receipt_record,
                "related_requisition": related_requisition,
                "can_view_requisition": can_view_requisition,
                "return_query": return_query,
            },
        )


class NotificationTemplateListView(LoginRequiredMixin, View):
    """
    Management screen for SMS Notification Templates.
    Allows authorized administrators to inspect, search, filter, and preview
    active and draft templates across purposes (Thank You, Receipt) and contribution categories.
    """

    def get(self, request):
        if not check_template_manage_permission(request.user):
            raise PermissionDenied("You do not have permission to manage SMS templates.")

        qs = NotificationTemplate.objects.select_related(
            "contribution_type", "created_by", "updated_by"
        ).order_by("notification_type", "name")

        ntype = request.GET.get("type", "").strip()
        if ntype in NotificationType.values:
            qs = qs.filter(notification_type=ntype)

        category_id = request.GET.get("category", "").strip()
        if category_id:
            if category_id == "generic":
                qs = qs.filter(contribution_type__isnull=True)
            else:
                qs = qs.filter(contribution_type_id=category_id)

        active_filter = request.GET.get("active", "").strip()
        if active_filter == "1":
            qs = qs.filter(is_active=True)
        elif active_filter == "0":
            qs = qs.filter(is_active=False)

        # Generate sample preview for each template
        templates_with_previews = []
        for tmpl in qs:
            preview_text, is_valid, error = NotificationTemplateService.render_preview(tmpl.template_body)
            templates_with_previews.append({
                "template": tmpl,
                "preview": preview_text,
                "is_valid": is_valid,
                "error": error,
            })

        categories = ContributionType.objects.filter(is_active=True).order_by("name")

        return render(
            request,
            "notifications/template_list.html",
            {
                "templates_data": templates_with_previews,
                "notification_types": NotificationType.choices,
                "categories": categories,
                "selected_type": ntype,
                "selected_category": category_id,
                "selected_active": active_filter,
            },
        )


class NotificationTemplateCreateView(LoginRequiredMixin, View):
    """Creates a new configurable SMS template with instant placeholder validation."""

    def get(self, request):
        if not check_template_manage_permission(request.user):
            raise PermissionDenied("You do not have permission to create SMS templates.")

        form = NotificationTemplateForm()
        initial_body = "Dear {first_name}, thank you for your contribution of {amount} {currency} ({contribution_type}) to TCI Higher Life Center on {date}. God bless you!"
        form.initial["template_body"] = initial_body
        preview_text, _, _ = NotificationTemplateService.render_preview(initial_body)

        return render(
            request,
            "notifications/template_form.html",
            {
                "form": form,
                "is_edit": False,
                "preview_text": preview_text,
                "allowed_placeholders": sorted(NotificationTemplateService.ALLOWED_PLACEHOLDERS),
            },
        )

    def post(self, request):
        if not check_template_manage_permission(request.user):
            raise PermissionDenied("You do not have permission to create SMS templates.")

        form = NotificationTemplateForm(request.POST)
        if form.is_valid():
            template = form.save(commit=False)
            template.created_by = request.user
            template.updated_by = request.user

            if template.is_active:
                NotificationTemplateService.save_and_activate_template(template, actor=request.user)
            else:
                template.save()
                AuditService.log(
                    action="SMS_TEMPLATE_CREATED",
                    entity_type="NotificationTemplate",
                    entity_id=template.id,
                    user=request.user,
                    new_values={
                        "name": template.name,
                        "notification_type": template.notification_type,
                        "is_active": False,
                    },
                    result=AuditResult.SUCCESS,
                )

            messages.success(request, f"SMS Template '{template.name}' saved successfully.")
            return redirect("notifications:template_list")

        preview_text, _, _ = NotificationTemplateService.render_preview(
            request.POST.get("template_body", "")
        )
        return render(
            request,
            "notifications/template_form.html",
            {
                "form": form,
                "is_edit": False,
                "preview_text": preview_text,
                "allowed_placeholders": sorted(NotificationTemplateService.ALLOWED_PLACEHOLDERS),
            },
        )


class NotificationTemplateUpdateView(LoginRequiredMixin, View):
    """Edits an existing SMS template. Updates affect only future notifications."""

    def get(self, request, template_id):
        if not check_template_manage_permission(request.user):
            raise PermissionDenied("You do not have permission to edit SMS templates.")

        template = get_object_or_404(NotificationTemplate, id=template_id)
        form = NotificationTemplateForm(instance=template)
        preview_text, _, _ = NotificationTemplateService.render_preview(template.template_body)

        return render(
            request,
            "notifications/template_form.html",
            {
                "form": form,
                "is_edit": True,
                "template": template,
                "preview_text": preview_text,
                "allowed_placeholders": sorted(NotificationTemplateService.ALLOWED_PLACEHOLDERS),
            },
        )

    def post(self, request, template_id):
        if not check_template_manage_permission(request.user):
            raise PermissionDenied("You do not have permission to edit SMS templates.")

        template = get_object_or_404(NotificationTemplate, id=template_id)
        old_body = template.template_body
        old_active = template.is_active

        form = NotificationTemplateForm(request.POST, instance=template)
        if form.is_valid():
            updated = form.save(commit=False)
            updated.updated_by = request.user

            if updated.is_active:
                NotificationTemplateService.save_and_activate_template(updated, actor=request.user)
            else:
                updated.save()
                AuditService.log(
                    action="SMS_TEMPLATE_UPDATED",
                    entity_type="NotificationTemplate",
                    entity_id=updated.id,
                    user=request.user,
                    old_values={"template_body": old_body, "is_active": old_active},
                    new_values={"template_body": updated.template_body, "is_active": False},
                    result=AuditResult.SUCCESS,
                )

            messages.success(request, f"SMS Template '{updated.name}' updated successfully.")
            return redirect("notifications:template_list")

        preview_text, _, _ = NotificationTemplateService.render_preview(
            request.POST.get("template_body", "")
        )
        return render(
            request,
            "notifications/template_form.html",
            {
                "form": form,
                "is_edit": True,
                "template": template,
                "preview_text": preview_text,
                "allowed_placeholders": sorted(NotificationTemplateService.ALLOWED_PLACEHOLDERS),
            },
        )


class NotificationTemplateToggleActiveView(LoginRequiredMixin, View):
    """Activates or deactivates an SMS template with ambiguous duplicate prevention."""

    def post(self, request, template_id):
        if not check_template_manage_permission(request.user):
            raise PermissionDenied("You do not have permission to modify SMS templates.")

        template = get_object_or_404(NotificationTemplate, id=template_id)
        if not template.is_active:
            NotificationTemplateService.save_and_activate_template(template, actor=request.user)
            messages.success(request, f"SMS Template '{template.name}' is now active.")
        else:
            template.is_active = False
            template.updated_by = request.user
            template.save(update_fields=["is_active", "updated_by", "updated_at"])
            AuditService.log(
                action="SMS_TEMPLATE_DEACTIVATED",
                entity_type="NotificationTemplate",
                entity_id=template.id,
                user=request.user,
                new_values={"is_active": False},
                result=AuditResult.SUCCESS,
            )
            messages.info(request, f"SMS Template '{template.name}' has been deactivated.")

        return redirect("notifications:template_list")


class NotificationTemplatePreviewApiView(LoginRequiredMixin, View):
    """Live preview JSON endpoint to test wording against sample data without sending SMS."""

    def post(self, request):
        if not check_template_manage_permission(request.user):
            return JsonResponse({"valid": False, "error": "Unauthorized"}, status=403)

        body = request.POST.get("template_body", "").strip()
        if not body:
            return JsonResponse({"valid": False, "error": "Template body is empty."})

        rendered, is_valid, error = NotificationTemplateService.render_preview(body)
        return JsonResponse({
            "valid": is_valid,
            "rendered": rendered,
            "error": error or "",
        })
