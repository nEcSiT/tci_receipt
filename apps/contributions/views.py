import datetime
from decimal import Decimal
from django.contrib import messages
from django.core.exceptions import ValidationError, PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Sum, Q
from django.http import JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.views import View

from apps.audit.models import AuditLog
from apps.contributions.models import (
    Contribution,
    ContributionType,
    PaymentMode,
    EntryMethod,
    ContributionStatus,
)
from apps.contributions.services import ContributionService
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
