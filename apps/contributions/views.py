import datetime
import json
import hashlib
import hmac
import uuid
from decimal import Decimal
from django.contrib import messages
from django.core.exceptions import ValidationError, PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Sum, Q
from django.http import JsonResponse, HttpResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.conf import settings
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
    BulkContributionService,
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

        contribution_types = ContributionType.get_recording_categories()
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
        coverage_year = request.POST.get("coverage_year", "").strip()
        coverage_months = request.POST.getlist("coverage_months")

        contribution_types = ContributionType.get_recording_categories()
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
                coverage_year=coverage_year or None,
                coverage_months=coverage_months or None,
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
        if not (check_create_permission(request.user) or check_view_permission(request.user)):
            return JsonResponse({"error": "Forbidden: You do not have permission to search members."}, status=403)

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
        if not provider.verify_webhook(request.body, {key: value for key, value in request.headers.items()}):
            return JsonResponse({"error": "Webhook authentication failed."}, status=401)
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
        if not settings.DEBUG:
            secret = getattr(settings, "USSD_GATEWAY_SECRET", "")
            signature = request.headers.get("X-USSD-Signature", "")
            expected = hmac.new(secret.encode("utf-8"), request.body, hashlib.sha256).hexdigest() if secret else ""
            if not secret or not signature or not hmac.compare_digest(signature, expected):
                return JsonResponse({"error": "USSD gateway authentication failed."}, status=401)

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


class BulkContributionEntryView(View):
    """
    On-screen multi-row table entry workflow for recording multiple contributions.
    Supports church contributions and Welfare through shared validation and atomic saving.
    """

    def get(self, request):
        if not request.user.is_authenticated:
            return redirect(f"/login/?next={request.path}")
        if not check_create_permission(request.user):
            raise PermissionDenied("You do not have permission to record contributions.")

        # Clear any old preview session
        request.session.pop("bulk_contrib_valid_rows", None)
        request.session.pop("bulk_contrib_idempotency_token", None)

        contribution_types = ContributionType.get_recording_categories()
        today = timezone.localdate()

        return render(
            request,
            "contributions/bulk_entry.html",
            {
                "contribution_types": contribution_types,
                "payment_modes": PaymentMode.choices,
                "today_date": today.isoformat(),
                "current_year": today.year,
                "initial_type": request.GET.get("type", ""),
            },
        )

    def post(self, request):
        if not request.user.is_authenticated:
            return redirect(f"/login/?next={request.path}")
        if not check_create_permission(request.user):
            raise PermissionDenied("You do not have permission to record contributions.")

        # Parse submitted rows: can be JSON string or array parameters
        rows_json = request.POST.get("rows_json")
        rows_data = []

        if rows_json:
            try:
                rows_data = json.loads(rows_json)
            except Exception:
                rows_data = []
        else:
            # Parse form array
            member_ids = request.POST.getlist("row_member_id")
            member_idents = request.POST.getlist("row_member_identifier")
            category_ids = request.POST.getlist("row_category_id")
            amounts = request.POST.getlist("row_amount")
            payment_modes = request.POST.getlist("row_payment_mode")
            payment_dates = request.POST.getlist("row_payment_date")
            references = request.POST.getlist("row_reference")
            descriptions = request.POST.getlist("row_description")
            welfare_years = request.POST.getlist("row_welfare_year")
            welfare_months_list = request.POST.getlist("row_welfare_months")

            count = max(len(amounts), len(member_ids), len(category_ids))
            for i in range(count):
                rows_data.append({
                    "member_id": member_ids[i] if i < len(member_ids) else "",
                    "member_identifier": member_idents[i] if i < len(member_idents) else "",
                    "category_id": category_ids[i] if i < len(category_ids) else "",
                    "amount": amounts[i] if i < len(amounts) else "",
                    "currency": "GHS",
                    "payment_mode": payment_modes[i] if i < len(payment_modes) else "CASH",
                    "payment_date": payment_dates[i] if i < len(payment_dates) else "",
                    "reference_number": references[i] if i < len(references) else "",
                    "custom_type_description": descriptions[i] if i < len(descriptions) else "",
                    "coverage_year": welfare_years[i] if i < len(welfare_years) else "",
                    "coverage_months": welfare_months_list[i] if i < len(welfare_months_list) else "",
                })

        if not rows_data:
            messages.error(request, "Please enter at least one contribution row.")
            return redirect("contributions:bulk_entry")

        preview_data = BulkContributionService.parse_and_validate_rows(rows_data, actor=request.user)

        batch = BulkContributionService.create_batch_record(actor=request.user, preview_data=preview_data)
        submission_token = batch.submission_token
        request.session["bulk_contrib_submission_token"] = submission_token
        request.session["bulk_contrib_idempotency_token"] = submission_token

        return render(
            request,
            "contributions/bulk_preview.html",
            {
                "source": "on-screen",
                "preview_data": preview_data,
                "valid_rows_count": preview_data["valid_count"],
                "submission_token": submission_token,
                "idempotency_token": submission_token,
            },
        )


class BulkContributionUploadView(View):
    """
    Excel spreadsheet upload workflow for batch church contributions and Welfare.
    Validates all rows, detects member matches and conflicts, and presents a review screen.
    """

    def get(self, request):
        if not request.user.is_authenticated:
            return redirect(f"/login/?next={request.path}")
        if not check_create_permission(request.user):
            raise PermissionDenied("You do not have permission to record contributions.")

        request.session.pop("bulk_contrib_valid_rows", None)
        request.session.pop("bulk_contrib_idempotency_token", None)

        return render(
            request,
            "contributions/bulk_upload.html",
            {},
        )

    def post(self, request):
        if not request.user.is_authenticated:
            return redirect(f"/login/?next={request.path}")
        if not check_create_permission(request.user):
            raise PermissionDenied("You do not have permission to record contributions.")

        uploaded_file = request.FILES.get("excel_file")
        if not uploaded_file:
            messages.error(request, "Please select an Excel (.xlsx) file to upload.")
            return redirect("contributions:bulk_upload")

        if not uploaded_file.name.endswith(".xlsx"):
            messages.error(request, "Please upload a valid Microsoft Excel file (.xlsx format).")
            return redirect("contributions:bulk_upload")

        try:
            preview_data = BulkContributionService.parse_excel(uploaded_file, actor=request.user)
        except ValidationError as e:
            error_msg = "; ".join(e.messages) if hasattr(e, "messages") else str(e)
            messages.error(request, error_msg)
            return redirect("contributions:bulk_upload")

        batch = BulkContributionService.create_batch_record(actor=request.user, preview_data=preview_data)
        submission_token = batch.submission_token
        request.session["bulk_contrib_submission_token"] = submission_token
        request.session["bulk_contrib_idempotency_token"] = submission_token

        return render(
            request,
            "contributions/bulk_preview.html",
            {
                "source": "excel",
                "filename": uploaded_file.name,
                "preview_data": preview_data,
                "valid_rows_count": preview_data["valid_count"],
                "submission_token": submission_token,
                "idempotency_token": submission_token,
            },
        )


class BulkContributionTemplateView(View):
    """Downloads the official Excel template for bulk contributions."""

    def get(self, request):
        if not request.user.is_authenticated:
            return redirect(f"/login/?next={request.path}")
        if not check_create_permission(request.user):
            raise PermissionDenied("You do not have permission to record contributions.")

        template_bytes = BulkContributionService.generate_template()

        response = HttpResponse(
            template_bytes,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response["Content-Disposition"] = 'attachment; filename="tci_bulk_contributions_template.xlsx"'
        return response


class BulkContributionConfirmView(View):
    """
    Confirms and commits the batch contributions inside an atomic database transaction.
    Generates individual official receipts, dispatches thank-you SMS, and provides results.
    """

    def post(self, request):
        if not request.user.is_authenticated:
            return redirect(f"/login/?next={request.path}")
        if not check_create_permission(request.user):
            raise PermissionDenied("You do not have permission to record contributions.")

        submission_token = (
            request.POST.get("submission_token")
            or request.POST.get("idempotency_token")
            or request.session.get("bulk_contrib_submission_token")
        )

        if not submission_token or not str(submission_token).strip():
            messages.error(request, "No pending contribution batch was found. Please re-enter or re-upload your contributions.")
            return redirect("contributions:bulk_entry")

        try:
            result = BulkContributionService.execute_batch(
                actor=request.user,
                submission_token=submission_token.strip(),
            )

            # Clear session
            request.session.pop("bulk_contrib_valid_rows", None)
            request.session.pop("bulk_contrib_submission_token", None)
            request.session.pop("bulk_contrib_idempotency_token", None)

            if result.get("is_replay"):
                messages.info(
                    request,
                    f"This batch has already been processed ({result['count']} contributions, GHS {result['total_amount']:.2f}). "
                    f"Displaying existing receipts."
                )
            else:
                messages.success(
                    request,
                    f"Successfully recorded {result['count']} contributions (GHS {result['total_amount']:.2f}) "
                    f"and generated individual receipts."
                )

            return render(
                request,
                "contributions/bulk_results.html",
                {
                    "result": result,
                },
            )

        except (ValidationError, PermissionDenied) as e:
            error_msg = "; ".join(e.messages) if hasattr(e, "messages") else str(e)
            messages.error(request, error_msg)
            return redirect("contributions:bulk_entry")


class WelfareOverviewView(View):
    """
    Dedicated Welfare overview page backed by the same underlying contribution records.
    Displays member search, year/month filters, payment history, recorded monthly coverage grid,
    and actions to record welfare individually or in bulk.
    """

    def get(self, request):
        if not request.user.is_authenticated:
            return redirect(f"/login/?next={request.path}")
        if not check_view_permission(request.user):
            raise PermissionDenied("You do not have permission to view welfare financial records.")

        from apps.contributions.models import WelfareCoverage

        welfare_type = ContributionType.objects.filter(name__iexact="welfare").first()
        current_year = timezone.localdate().year

        year_param = request.GET.get("year", "").strip()
        try:
            selected_year = int(year_param) if year_param else current_year
        except ValueError:
            selected_year = current_year

        month_param = request.GET.get("month", "").strip()
        try:
            selected_month = int(month_param) if month_param else None
            if selected_month and not (1 <= selected_month <= 12):
                selected_month = None
        except ValueError:
            selected_month = None

        query = request.GET.get("q", "").strip()
        date_from = request.GET.get("date_from", "").strip()
        date_to = request.GET.get("date_to", "").strip()

        # Query contributions
        qs = Contribution.objects.filter(contribution_type=welfare_type)

        if query:
            search_q = (
                Q(member__first_name__icontains=query) |
                Q(member__last_name__icontains=query) |
                Q(member__member_number__icontains=query) |
                Q(member__phone_numbers__phone_number__icontains=query) |
                Q(contribution_number__icontains=query)
            )
            from apps.core.utils.phone import clean_phone_number, get_phone_lookup_variants
            cleaned_phone = clean_phone_number(query)
            if cleaned_phone:
                for variant in get_phone_lookup_variants(cleaned_phone):
                    search_q |= Q(member__phone_numbers__phone_number__icontains=variant)
            qs = qs.filter(search_q)

        if date_from:
            qs = qs.filter(contribution_date__gte=date_from)
        if date_to:
            qs = qs.filter(contribution_date__lte=date_to)

        if selected_month:
            qs = qs.filter(
                welfare_coverages__year=selected_year,
                welfare_coverages__month=selected_month,
            )
        elif selected_year:
            qs = qs.filter(
                welfare_coverages__year=selected_year,
            )

        contributions = (
            qs.distinct()
            .select_related("member", "receipt", "recorded_by")
            .prefetch_related("welfare_coverages")
            .order_by("-contribution_date", "-created_at")
        )

        # Pagination: 25 items per page
        paginator = Paginator(contributions, 25)
        page_number = request.GET.get("page")
        page_obj = paginator.get_page(page_number)

        # Summary Metrics:
        # PRD rule: Actual positive amount received counted strictly ONCE (never multiplied by covered months)
        total_welfare_amount = sum((c.amount for c in contributions), Decimal("0.00"))
        transactions_count = contributions.count()
        unique_members_count = contributions.values("member").distinct().count()

        # Total coverage months count across selected contributions
        covered_slots_count = WelfareCoverage.objects.filter(
            contribution__in=contributions,
            year=selected_year,
        ).count()

        # Monthly Coverage Map by contributing member: {member_id: set([months])}
        member_coverage = {}
        contributing_members = []
        for c in contributions:
            if c.member and c.member.id not in member_coverage:
                member_coverage[c.member.id] = set()
                contributing_members.append(c.member)
            if c.member:
                for cov in c.welfare_coverages.all():
                    if cov.year == selected_year:
                        member_coverage[c.member.id].add(cov.month)

        member_coverage_rows = []
        for m in contributing_members[:50]:
            months_set = member_coverage.get(m.id, set())
            member_coverage_rows.append({
                "member": m,
                "months": {month_num: (month_num in months_set) for month_num in range(1, 13)},
                "total_covered_months": len(months_set),
            })

        month_names = [
            (1, "January"), (2, "February"), (3, "March"), (4, "April"),
            (5, "May"), (6, "June"), (7, "July"), (8, "August"),
            (9, "September"), (10, "October"), (11, "November"), (12, "December")
        ]

        # Year options: current year - 3 to current year + 2
        years_list = list(range(current_year - 3, current_year + 3))

        return render(
            request,
            "contributions/welfare_overview.html",
            {
                "page_obj": page_obj,
                "contributions": page_obj.object_list,
                "total_welfare_amount": total_welfare_amount,
                "transactions_count": transactions_count,
                "unique_members_count": unique_members_count,
                "covered_slots_count": covered_slots_count,
                "member_coverage_rows": member_coverage_rows,
                "selected_year": selected_year,
                "selected_month": selected_month,
                "query": query,
                "date_from": date_from,
                "date_to": date_to,
                "month_names": month_names,
                "years_list": years_list,
            },
        )

