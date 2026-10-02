import datetime
import json
from decimal import Decimal
from django.contrib import messages
from django.core.exceptions import ValidationError, PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Sum, Q
from django.http import JsonResponse, HttpResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from apps.audit.models import AuditLog
from apps.contributions.models import (
    Contribution,
    ContributionType,
    PaymentMode,
    EntryMethod,
    ContributionStatus,
)
from apps.contributions.providers.factory import get_payment_provider
from apps.contributions.services import (
    ContributionService,
    AutomaticPaymentService,
    UssdService,
)
from apps.members.models import Member, MemberStatus


def check_view_permission(user) -> bool:
    """Verifies that the user has permission to view contributions."""
    if not user.is_authenticated or not user.is_active:
        return False
    return (
        user.is_system_administrator
        or user.has_permission("contribution.view")
        or user.has_permission("contribution.create")
        or user.has_permission("contribution.edit_own")
    )


def check_create_permission(user) -> bool:
    """Verifies that the user has permission to record contributions."""
    if not user.is_authenticated or not user.is_active:
        return False
    return user.is_system_administrator or user.has_permission("contribution.create")


class ContributionListView(View):
    """
    Searchable, filterable, and paginated view of all church contribution records.
    Displays operational KPI summaries and receipt cross-reference links.
    """

    def get(self, request):
        if not request.user.is_authenticated:
            return redirect(f"/login/?next={request.path}")
        if not check_view_permission(request.user):
            raise PermissionDenied("You do not have permission to view contributions.")

        query = request.GET.get("q", "").strip()
        type_filter = request.GET.get("type", "all")
        mode_filter = request.GET.get("mode", "all")
        entry_method_filter = request.GET.get("entry_method", "all")
        status_filter = request.GET.get("status", "all")
        date_from = request.GET.get("date_from", "").strip()
        date_to = request.GET.get("date_to", "").strip()
        order_by = request.GET.get("order_by", "-contribution_date").strip()

        contributions_qs = ContributionService.search_contributions(
            query=query,
            contribution_type_id=type_filter if type_filter != "all" else None,
            payment_mode=mode_filter if mode_filter != "all" else None,
            entry_method=entry_method_filter if entry_method_filter != "all" else None,
            status=status_filter if status_filter != "all" else None,
            date_from=date_from or None,
            date_to=date_to or None,
            order_by=order_by,
        )

        paginator = Paginator(contributions_qs, 25)
        page_number = request.GET.get("page", 1)
        page_obj = paginator.get_page(page_number)

        # Operational KPI metrics
        total_count = Contribution.objects.count()
        total_amount = (
            Contribution.objects.filter(status=ContributionStatus.CONFIRMED).aggregate(Sum("amount"))["amount__sum"]
            or Decimal("0.00")
        )
        manual_count = Contribution.objects.filter(entry_method=EntryMethod.MANUAL).count()
        automatic_count = Contribution.objects.filter(entry_method=EntryMethod.AUTOMATIC).count()

        contribution_types = ContributionType.objects.filter(is_active=True)

        return render(
            request,
            "contributions/contribution_list.html",
            {
                "page_obj": page_obj,
                "query": query,
                "type_filter": type_filter,
                "mode_filter": mode_filter,
                "entry_method_filter": entry_method_filter,
                "status_filter": status_filter,
                "date_from": date_from,
                "date_to": date_to,
                "order_by": order_by,
                "total_count": total_count,
                "total_amount": total_amount,
                "manual_count": manual_count,
                "automatic_count": automatic_count,
                "contribution_types": contribution_types,
                "payment_modes": PaymentMode.choices,
                "entry_methods": EntryMethod.choices,
                "statuses": ContributionStatus.choices,
            },
        )


class ContributionCreateView(View):
    """
    Manual church contribution recording workflow for authorized System Users.
    Integrates member search, dynamic reference labels, and immediate validation feedback.
    """

    def get(self, request):
        if not request.user.is_authenticated:
            return redirect(f"/login/?next={request.path}")
        if not check_create_permission(request.user):
            raise PermissionDenied("You do not have permission to record contributions.")

        # Check if pre-selected member was passed in query param
        preselected_member = None
        member_id = request.GET.get("member")
        if member_id:
            preselected_member = Member.objects.filter(id=member_id, status=MemberStatus.ACTIVE).first()

        contribution_types = ContributionType.objects.filter(is_active=True)
        recent_members = Member.objects.filter(status=MemberStatus.ACTIVE).order_by("-created_at")[:50]

        return render(
            request,
            "contributions/contribution_form.html",
            {
                "contribution_types": contribution_types,
                "payment_modes": PaymentMode.choices,
                "recent_members": recent_members,
                "preselected_member": preselected_member,
                "form_data": {},
                "today_date": timezone.localdate().isoformat(),
            },
        )

    def post(self, request):
        if not request.user.is_authenticated:
            return redirect(f"/login/?next={request.path}")
        if not check_create_permission(request.user):
            raise PermissionDenied("You do not have permission to record contributions.")

        member_id = request.POST.get("member_id", "").strip()
        type_id = request.POST.get("contribution_type_id", "").strip()
        custom_description = request.POST.get("custom_type_description", "").strip()
        amount_raw = request.POST.get("amount", "").strip()
        payment_mode = request.POST.get("payment_mode", "").strip()
        reference_number = request.POST.get("reference_number", "").strip()
        contribution_date = request.POST.get("contribution_date", "").strip()

        contribution_types = ContributionType.objects.filter(is_active=True)
        recent_members = Member.objects.filter(status=MemberStatus.ACTIVE).order_by("-created_at")[:50]

        # Resolve selected member
        member = None
        if member_id:
            member = Member.objects.filter(id=member_id).first()

        # Resolve selected contribution type
        contribution_type = None
        if type_id:
            contribution_type = ContributionType.objects.filter(id=type_id, is_active=True).first()

        try:
            if not member:
                raise ValidationError("Please search and select a valid church member.")

            if not contribution_type:
                raise ValidationError("Please select a contribution type.")

            contribution = ContributionService.record_manual_contribution(
                recorder=request.user,
                member=member,
                contribution_type=contribution_type,
                amount=amount_raw,
                payment_mode=payment_mode,
                reference_number=reference_number or None,
                custom_type_description=custom_description or None,
                contribution_date=contribution_date or None,
            )

            messages.success(
                request,
                f"Contribution {contribution.contribution_number} (GHS {contribution.amount}) "
                f"for {member.full_name} recorded successfully.",
            )
            return redirect("contributions:contribution_detail", contribution_id=contribution.id)

        except (ValidationError, PermissionDenied) as e:
            error_msg = "; ".join(e.messages) if hasattr(e, "messages") else str(e)
            return render(
                request,
                "contributions/contribution_form.html",
                {
                    "error": error_msg,
                    "form_data": request.POST,
                    "preselected_member": member,
                    "contribution_types": contribution_types,
                    "payment_modes": PaymentMode.choices,
                    "recent_members": recent_members,
                    "today_date": timezone.localdate().isoformat(),
                },
                status=400,
            )


class ContributionDetailView(View):
    """
    Displays full details of a Contribution, contributor details, payment mode,
    financial status, system recording details, related transactions, and audit history.
    """

    def get(self, request, contribution_id):
        if not request.user.is_authenticated:
            return redirect(f"/login/?next={request.path}")
        if not check_view_permission(request.user):
            raise PermissionDenied("You do not have permission to view contribution details.")

        contribution = get_object_or_404(
            Contribution.objects.select_related(
                "member",
                "temporary_contributor",
                "contribution_type",
                "payment_transaction",
                "recorded_by",
            ).prefetch_related("member__phone_numbers"),
            id=contribution_id,
        )

        receipt = getattr(contribution, "receipt", None)

        audit_logs = AuditLog.objects.filter(
            entity_type="Contribution", entity_id=contribution.id
        ).order_by("-created_at")

        return render(
            request,
            "contributions/contribution_detail.html",
            {
                "contribution": contribution,
                "receipt": receipt,
                "audit_logs": audit_logs,
            },
        )


class MemberSearchApiView(View):
    """
    Lightweight JSON endpoint allowing the contribution creation interface to quickly
    search active church members by name, member number, or phone number.
    """

    def get(self, request):
        if not request.user.is_authenticated:
            return JsonResponse({"error": "Unauthorized"}, status=401)

        q = request.GET.get("q", "").strip()
        if not q or len(q) < 2:
            return JsonResponse({"results": []})

        members = (
            Member.objects.filter(status=MemberStatus.ACTIVE)
            .filter(
                Q(member_number__icontains=q)
                | Q(first_name__icontains=q)
                | Q(middle_name__icontains=q)
                | Q(last_name__icontains=q)
                | Q(email__icontains=q)
                | Q(phone_numbers__phone_number__icontains=q)
            )
            .prefetch_related("phone_numbers")
            .distinct()[:20]
        )

        results = []
        for m in members:
            primary_phone = m.phone_numbers.filter(is_primary=True, is_active=True).first()
            results.append(
                {
                    "id": str(m.id),
                    "member_number": m.member_number,
                    "full_name": m.full_name,
                    "email": m.email or "",
                    "phone": primary_phone.phone_number if primary_phone else "",
                    "ministry": m.ministry or "",
                }
            )

        return JsonResponse({"results": results})


@method_decorator(csrf_exempt, name="dispatch")
class PaymentWebhookView(View):
    """
    Receives and processes incoming payment provider webhook callbacks.
    Decoupled via Provider Abstraction.
    """

    def post(self, request, *args, **kwargs):
        try:
            if request.content_type == "application/json":
                payload = json.loads(request.body.decode("utf-8"))
            else:
                payload = request.POST.dict()
        except Exception as e:
            return JsonResponse({"error": f"Invalid payload: {str(e)}"}, status=400)

        provider = get_payment_provider()
        payment_result = provider.parse_webhook(payload)

        # Extract contribution type or default to General / Tithe
        c_type_name = payload.get("contribution_type") or payload.get("type") or "Tithe"
        desc = payload.get("custom_type_description") or payload.get("description")

        try:
            result = AutomaticPaymentService.process_payment_confirmation(
                provider_reference=payment_result.provider_reference,
                transaction_phone=payment_result.transaction_phone,
                amount=payment_result.amount,
                contribution_type_name=c_type_name,
                custom_type_description=desc,
                status=payment_result.status,
                provider=payment_result.provider,
                provider_name=payment_result.provider_name,
                failure_code=payment_result.failure_code,
                failure_reason=payment_result.failure_reason,
                provider_data=payment_result.provider_data,
            )
            return JsonResponse({
                "status": result["status"],
                "provider_reference": payment_result.provider_reference,
                "contribution_number": result["contribution"].contribution_number if result.get("contribution") else None,
                "receipt_number": result["receipt"].receipt_number if result.get("receipt") else None,
            })
        except Exception as e:
            return JsonResponse({"error": str(e)}, status=422)


@method_decorator(csrf_exempt, name="dispatch")
class UssdGatewayView(View):
    """
    HTTP Gateway endpoint for telecom USSD aggregation providers (Hubtel, Africa's Talking).
    Initial menu response begins strictly with: 'Welcome to TCI HLC Givings'
    """

    def dispatch(self, request, *args, **kwargs):
        session_id = request.GET.get("sessionId") or request.POST.get("sessionId") or "session-demo"
        phone = request.GET.get("phoneNumber") or request.POST.get("phoneNumber") or "0240000001"
        text = request.GET.get("text") if "text" in request.GET else request.POST.get("text", "")

        result = UssdService.handle_request(
            session_id=session_id,
            phone_number=phone,
            text=text,
            simulate_success=True,
        )

        prefix = "END " if result["is_terminal"] else "CON "
        response_text = f"{prefix}{result['message']}"

        if "application/json" in request.headers.get("Accept", ""):
            return JsonResponse({
                "message": result["message"],
                "is_terminal": result["is_terminal"],
                "ussd_string": response_text,
            })

        return HttpResponse(response_text, content_type="text/plain")
