import uuid
from django.contrib import messages
from django.core.exceptions import ValidationError, PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Q, Count
from django.shortcuts import render, redirect, get_object_or_404
from django.views import View

from apps.accounts.models import User
from apps.contributions.models import Contribution
from apps.members.models import (
    Member,
    MemberPhoneNumber,
    TemporaryContributor,
    MemberMergeRequest,
    MemberStatus,
    TemporaryContributorStatus,
    MemberMergeStatus,
)
from apps.members.services import (
    MemberService,
    TemporaryContributorService,
    MemberMergeService,
)


class MemberListView(View):
    """Lists church members with multi-criteria search and status filters."""

    def get(self, request):
        if not request.user.is_authenticated:
            return redirect(f"/login/?next={request.path}")

        query = request.GET.get("q", "").strip()
        status_filter = request.GET.get("status", "all")
        ministry_filter = request.GET.get("ministry", "all")

        members = Member.objects.prefetch_related("phone_numbers").all()

        if query:
            members = members.filter(
                Q(member_number__icontains=query) |
                Q(first_name__icontains=query) |
                Q(middle_name__icontains=query) |
                Q(last_name__icontains=query) |
                Q(email__icontains=query) |
                Q(phone_numbers__phone_number__icontains=query)
            ).distinct()

        if status_filter != "all":
            members = members.filter(status=status_filter.upper())

        if ministry_filter != "all":
            members = members.filter(ministry__iexact=ministry_filter)

        paginator = Paginator(members, 20)
        page_number = request.GET.get("page", 1)
        page_obj = paginator.get_page(page_number)

        # Quick stats for operational header
        total_count = Member.objects.count()
        active_count = Member.objects.filter(status=MemberStatus.ACTIVE).count()
        unverified_tc_count = TemporaryContributor.objects.filter(
            status=TemporaryContributorStatus.UNVERIFIED
        ).count()
        pending_merges_count = MemberMergeRequest.objects.filter(
            status=MemberMergeStatus.PENDING
        ).count()

        ministries = (
            Member.objects.exclude(ministry__isnull=True)
            .exclude(ministry="")
            .values_list("ministry", flat=True)
            .distinct()
            .order_by("ministry")
        )

        return render(request, "members/member_list.html", {
            "page_obj": page_obj,
            "query": query,
            "status_filter": status_filter,
            "ministry_filter": ministry_filter,
            "total_count": total_count,
            "active_count": active_count,
            "unverified_tc_count": unverified_tc_count,
            "pending_merges_count": pending_merges_count,
            "ministries": ministries,
        })


class MemberCreateView(View):
    """Registers a new permanent church member."""

    def get(self, request):
        if not request.user.is_authenticated:
            return redirect(f"/login/?next={request.path}")
        if not (request.user.is_system_administrator or request.user.has_permission("member.create")):
            raise PermissionDenied("You do not have permission to register new members.")

        return render(request, "members/member_form.html", {
            "is_create": True,
            "form_data": {},
        })

    def post(self, request):
        if not request.user.is_authenticated:
            return redirect(f"/login/?next={request.path}")
        if not (request.user.is_system_administrator or request.user.has_permission("member.create")):
            raise PermissionDenied("You do not have permission to register new members.")

        first_name = request.POST.get("first_name", "").strip()
        middle_name = request.POST.get("middle_name", "").strip()
        last_name = request.POST.get("last_name", "").strip()
        email = request.POST.get("email", "").strip()
        ministry = request.POST.get("ministry", "").strip()
        team = request.POST.get("team", "").strip()
        department = request.POST.get("department", "").strip()
        phone_number = request.POST.get("phone_number", "").strip()
        phone_provider = request.POST.get("phone_provider", "").strip()

        try:
            member = MemberService.create_member(
                creator=request.user,
                first_name=first_name,
                last_name=last_name,
                middle_name=middle_name,
                email=email,
                ministry=ministry,
                team=team,
                department=department,
                phone_number=phone_number or None,
                phone_provider=phone_provider or None,
                is_primary_phone=True,
            )
            messages.success(request, f"Member '{member.full_name}' ({member.member_number}) registered successfully.")
            return redirect("members:member_detail", member_id=member.id)
        except ValidationError as e:
            return render(request, "members/member_form.html", {
                "is_create": True,
                "error": "; ".join(e.messages) if hasattr(e, "messages") else str(e),
                "form_data": request.POST,
            }, status=400)


class MemberDetailView(View):
    """Displays comprehensive member profile, contact phones, and financial history foundation."""

    def get(self, request, member_id):
        if not request.user.is_authenticated:
            return redirect(f"/login/?next={request.path}")

        member = get_object_or_404(
            Member.objects.prefetch_related("phone_numbers", "merge_requests_as_source", "merge_requests_as_target"),
            id=member_id,
        )

        phones = member.phone_numbers.all().order_by("-is_primary", "-is_active", "created_at")
        contributions = Contribution.objects.filter(member=member).select_related("contribution_type").order_by("-created_at")[:10]
        merge_requests = MemberMergeRequest.objects.filter(
            Q(source_member=member) | Q(target_member=member)
        ).select_related("source_member", "target_member", "requested_by").order_by("-created_at")

        return render(request, "members/member_detail.html", {
            "member": member,
            "phones": phones,
            "contributions": contributions,
            "merge_requests": merge_requests,
        })


class MemberEditView(View):
    """Updates member demographic details."""

    def get(self, request, member_id):
        if not request.user.is_authenticated:
            return redirect(f"/login/?next={request.path}")
        if not (request.user.is_system_administrator or request.user.has_permission("member.edit")):
            raise PermissionDenied("You do not have permission to edit member details.")

        member = get_object_or_404(Member, id=member_id)
        return render(request, "members/member_form.html", {
            "is_create": False,
            "member": member,
            "form_data": {
                "first_name": member.first_name,
                "middle_name": member.middle_name or "",
                "last_name": member.last_name,
                "email": member.email or "",
                "ministry": member.ministry or "",
                "team": member.team or "",
                "department": member.department or "",
            },
        })

    def post(self, request, member_id):
        if not request.user.is_authenticated:
            return redirect(f"/login/?next={request.path}")
        if not (request.user.is_system_administrator or request.user.has_permission("member.edit")):
            raise PermissionDenied("You do not have permission to edit member details.")

        member = get_object_or_404(Member, id=member_id)

        first_name = request.POST.get("first_name", "").strip()
        middle_name = request.POST.get("middle_name", "").strip()
        last_name = request.POST.get("last_name", "").strip()
        email = request.POST.get("email", "").strip()
        ministry = request.POST.get("ministry", "").strip()
        team = request.POST.get("team", "").strip()
        department = request.POST.get("department", "").strip()

        try:
            MemberService.update_member(
                actor=request.user,
                member=member,
                first_name=first_name,
                last_name=last_name,
                middle_name=middle_name,
                email=email,
                ministry=ministry,
                team=team,
                department=department,
            )
            messages.success(request, f"Member '{member.full_name}' details updated.")
            return redirect("members:member_detail", member_id=member.id)
        except ValidationError as e:
            return render(request, "members/member_form.html", {
                "is_create": False,
                "member": member,
                "error": "; ".join(e.messages) if hasattr(e, "messages") else str(e),
                "form_data": request.POST,
            }, status=400)


class MemberPhoneAddView(View):
    """Adds an additional phone number to a member."""

    def post(self, request, member_id):
        if not request.user.is_authenticated:
            return redirect(f"/login/?next={request.path}")
        if not (request.user.is_system_administrator or request.user.has_permission("member.edit")):
            raise PermissionDenied("You do not have permission to manage phone numbers.")

        member = get_object_or_404(Member, id=member_id)
        phone_number = request.POST.get("phone_number", "").strip()
        provider = request.POST.get("provider", "").strip()
        is_primary = request.POST.get("is_primary") == "on"

        try:
            phone = MemberService.add_phone_number(
                actor=request.user,
                member=member,
                phone_number=phone_number,
                provider=provider or None,
                is_primary=is_primary,
            )
            messages.success(request, f"Phone number {phone.phone_number} added.")
        except ValidationError as e:
            messages.error(request, "; ".join(e.messages) if hasattr(e, "messages") else str(e))

        return redirect("members:member_detail", member_id=member.id)


class MemberPhoneActionView(View):
    """Handles actions on a member's phone number (set primary, toggle active)."""

    def post(self, request, member_id, phone_id):
        if not request.user.is_authenticated:
            return redirect(f"/login/?next={request.path}")
        if not (request.user.is_system_administrator or request.user.has_permission("member.edit")):
            raise PermissionDenied("You do not have permission to manage phone numbers.")

        member = get_object_or_404(Member, id=member_id)
        phone = get_object_or_404(MemberPhoneNumber, id=phone_id, member=member)
        action = request.POST.get("action")

        try:
            if action == "set_primary":
                MemberService.set_primary_phone(actor=request.user, member=member, phone_id=phone.id)
                messages.success(request, f"Phone number {phone.phone_number} is now primary.")
            elif action == "toggle_active":
                new_state = not phone.is_active
                MemberService.toggle_phone_status(actor=request.user, phone=phone, is_active=new_state)
                status_str = "activated" if new_state else "deactivated"
                messages.success(request, f"Phone number {phone.phone_number} {status_str}.")
        except ValidationError as e:
            messages.error(request, "; ".join(e.messages) if hasattr(e, "messages") else str(e))

        return redirect("members:member_detail", member_id=member.id)


class MemberStatusToggleView(View):
    """Deactivates or restores a church member."""

    def post(self, request, member_id):
        if not request.user.is_authenticated:
            return redirect(f"/login/?next={request.path}")
        if not (request.user.is_system_administrator or request.user.has_permission("member.edit")):
            raise PermissionDenied("You do not have permission to change member status.")

        member = get_object_or_404(Member, id=member_id)
        action = request.POST.get("action")
        reason = request.POST.get("reason", "").strip()

        try:
            if action == "deactivate":
                MemberService.deactivate_member(actor=request.user, member=member, reason=reason)
                messages.success(request, f"Member {member.full_name} has been deactivated.")
            elif action == "activate":
                MemberService.activate_member(actor=request.user, member=member)
                messages.success(request, f"Member {member.full_name} has been activated.")
        except ValidationError as e:
            messages.error(request, "; ".join(e.messages) if hasattr(e, "messages") else str(e))

        return redirect("members:member_detail", member_id=member.id)


class TemporaryContributorListView(View):
    """Lists unverified temporary contributors generated from automatic transactions."""

    def get(self, request):
        if not request.user.is_authenticated:
            return redirect(f"/login/?next={request.path}")

        status_filter = request.GET.get("status", "UNVERIFIED")
        query = request.GET.get("q", "").strip()

        tcs = TemporaryContributor.objects.all()

        if status_filter != "all":
            tcs = tcs.filter(status=status_filter)

        if query:
            tcs = tcs.filter(
                Q(phone_number__icontains=query) |
                Q(provider_name__icontains=query) |
                Q(provider_account_reference__icontains=query)
            )

        paginator = Paginator(tcs, 25)
        page_obj = paginator.get_page(request.GET.get("page", 1))

        return render(request, "members/temporary_list.html", {
            "page_obj": page_obj,
            "status_filter": status_filter,
            "query": query,
            "members": Member.objects.filter(status=MemberStatus.ACTIVE).order_by("first_name", "last_name"),
        })


class TemporaryContributorActionView(View):
    """Processes linking or conversion of a temporary contributor."""

    def post(self, request, tc_id):
        if not request.user.is_authenticated:
            return redirect(f"/login/?next={request.path}")

        tc = get_object_or_404(TemporaryContributor, id=tc_id)
        action = request.POST.get("action")

        try:
            if action == "under_review":
                TemporaryContributorService.mark_under_review(actor=request.user, temporary_contributor=tc)
                messages.success(request, f"Contributor {tc.phone_number} marked as Under Review.")

            elif action == "link":
                target_member_id = request.POST.get("target_member_id")
                target_member = get_object_or_404(Member, id=target_member_id)
                add_phone = request.POST.get("add_phone") == "on"
                TemporaryContributorService.link_to_member(
                    actor=request.user,
                    temporary_contributor=tc,
                    target_member=target_member,
                    add_phone=add_phone,
                )
                messages.success(request, f"Contributor {tc.phone_number} successfully linked to {target_member.full_name}.")
                return redirect("members:member_detail", member_id=target_member.id)

            elif action == "convert":
                first_name = request.POST.get("first_name", "").strip()
                last_name = request.POST.get("last_name", "").strip()
                email = request.POST.get("email", "").strip()
                ministry = request.POST.get("ministry", "").strip()
                new_member = TemporaryContributorService.convert_to_member(
                    actor=request.user,
                    temporary_contributor=tc,
                    first_name=first_name,
                    last_name=last_name,
                    email=email,
                    ministry=ministry,
                )
                messages.success(request, f"Contributor converted to member {new_member.full_name} ({new_member.member_number}).")
                return redirect("members:member_detail", member_id=new_member.id)

        except ValidationError as e:
            messages.error(request, "; ".join(e.messages) if hasattr(e, "messages") else str(e))

        return redirect("members:temporary_list")


class MemberMergeListView(View):
    """Lists pending and historical member merge requests."""

    def get(self, request):
        if not request.user.is_authenticated:
            return redirect(f"/login/?next={request.path}")

        status_filter = request.GET.get("status", "PENDING")
        requests_qs = MemberMergeRequest.objects.select_related(
            "source_member", "target_member", "requested_by", "reviewed_by"
        ).all()

        if status_filter != "all":
            requests_qs = requests_qs.filter(status=status_filter)

        active_members = Member.objects.filter(status=MemberStatus.ACTIVE).order_by("first_name", "last_name")

        return render(request, "members/merge_list.html", {
            "merge_requests": requests_qs,
            "status_filter": status_filter,
            "active_members": active_members,
        })


class MemberMergeCreateView(View):
    """Submits a new member merge request."""

    def post(self, request):
        if not request.user.is_authenticated:
            return redirect(f"/login/?next={request.path}")
        if not (request.user.is_system_administrator or request.user.has_permission("member.merge.request")):
            raise PermissionDenied("You do not have permission to request member merges.")

        source_member_id = request.POST.get("source_member_id")
        target_member_id = request.POST.get("target_member_id")
        reason = request.POST.get("reason", "").strip()

        source_member = get_object_or_404(Member, id=source_member_id)
        target_member = get_object_or_404(Member, id=target_member_id)

        try:
            merge_req = MemberMergeService.request_merge(
                requester=request.user,
                source_member=source_member,
                target_member=target_member,
                reason=reason,
            )
            messages.success(
                request,
                f"Merge request created: {source_member.member_number} -> {target_member.member_number}. Pending administrator approval."
            )
        except ValidationError as e:
            messages.error(request, "; ".join(e.messages) if hasattr(e, "messages") else str(e))

        return redirect("members:merge_list")


class MemberMergeReviewView(View):
    """Administrator-exclusive action to approve or reject a member merge."""

    def post(self, request, request_id):
        if not request.user.is_authenticated:
            return redirect(f"/login/?next={request.path}")
        if not request.user.is_system_administrator:
            raise PermissionDenied("Only the System Administrator may review member merge requests.")

        merge_req = get_object_or_404(MemberMergeRequest, id=request_id)
        decision = request.POST.get("decision")
        remarks = request.POST.get("remarks", "").strip()

        try:
            if decision == "approve":
                MemberMergeService.review_merge(
                    reviewer=request.user,
                    merge_request=merge_req,
                    approve=True,
                    remarks=remarks,
                )
                messages.success(request, f"Merge approved: {merge_req.source_member.member_number} merged into {merge_req.target_member.member_number}.")
            elif decision == "reject":
                MemberMergeService.review_merge(
                    reviewer=request.user,
                    merge_request=merge_req,
                    approve=False,
                    remarks=remarks,
                )
                messages.warning(request, f"Merge request for {merge_req.source_member.member_number} was rejected.")
        except ValidationError as e:
            messages.error(request, "; ".join(e.messages) if hasattr(e, "messages") else str(e))

        return redirect("members:merge_list")
