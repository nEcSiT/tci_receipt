import logging
import uuid
import hashlib
from django.db import transaction
from django.core.exceptions import ValidationError, PermissionDenied
from django.utils import timezone

from apps.accounts.models import User
from apps.audit.models import AuditResult
from apps.audit.services import AuditService
from apps.core.utils.phone import get_phone_lookup_variants, clean_phone_number
from apps.contributions.models import Contribution
from apps.members.models import (
    Member,
    MemberPhoneNumber,
    TemporaryContributor,
    MemberMergeRequest,
    ContributorLinkingRequest,
    MemberStatus,
    TemporaryContributorStatus,
    MemberMergeStatus,
    ContributorLinkingStatus,
    MemberImportBatch,
    MemberImportStatus,
)

logger = logging.getLogger(__name__)


class MemberService:
    """Service handling Member lifecycle, profile updates, and phone assignments."""

    @classmethod
    @transaction.atomic
    def create_member(
        cls,
        creator: User,
        first_name: str,
        last_name: str,
        middle_name: str = "",
        email: str = "",
        ministry: str = "",
        team: str = "",
        department: str = "",
        phone_number: str | None = None,
        phone_provider: str | None = None,
        is_primary_phone: bool = True,
    ) -> Member:
        """
        Creates a new church member with a system-generated member number.
        Optionally attaches the initial primary phone number.
        """
        if not creator.is_active or (not creator.is_system_administrator and not creator.has_permission("member.create")):
            raise ValidationError("You do not have permission to create members.")

        first_name = first_name.strip() if first_name else ""
        last_name = last_name.strip() if last_name else ""
        if not first_name or not last_name:
            raise ValidationError("First name and last name are required.")

        clean_email = email.strip().lower() if email else None
        if clean_email == "":
            clean_email = None

        member = Member.objects.create(
            first_name=first_name,
            middle_name=middle_name.strip() if middle_name else None,
            last_name=last_name,
            email=clean_email,
            ministry=ministry.strip() if ministry else None,
            team=team.strip() if team else None,
            department=department.strip() if department else None,
            status=MemberStatus.ACTIVE,
        )

        initial_phone = None
        if phone_number and phone_number.strip():
            clean_phone = phone_number.strip()
            # Enforce active phone uniqueness across members
            conflict = MemberPhoneNumber.objects.filter(
                phone_number=clean_phone,
                is_active=True,
            ).first()
            if conflict:
                raise ValidationError(
                    f"Phone number {clean_phone} is already actively assigned to member {conflict.member}."
                )

            initial_phone = MemberPhoneNumber.objects.create(
                member=member,
                phone_number=clean_phone,
                provider=phone_provider.strip() if phone_provider else None,
                is_primary=is_primary_phone,
                is_active=True,
            )

        AuditService.log(
            action="MEMBER_CREATED",
            entity_type="Member",
            entity_id=member.id,
            user=creator,
            new_values={
                "member_number": member.member_number,
                "full_name": member.full_name,
                "email": member.email,
                "phone_number": initial_phone.phone_number if initial_phone else None,
            },
            result=AuditResult.SUCCESS,
        )
        return member

    @classmethod
    def update_member(
        cls,
        actor: User,
        member: Member,
        first_name: str,
        last_name: str,
        middle_name: str = "",
        email: str = "",
        ministry: str = "",
        team: str = "",
        department: str = "",
    ) -> Member:
        """Updates member demographic and ministry information."""
        if not actor.is_active or (not actor.is_system_administrator and not actor.has_permission("member.edit")):
            raise ValidationError("You do not have permission to edit members.")

        first_name = first_name.strip() if first_name else ""
        last_name = last_name.strip() if last_name else ""
        if not first_name or not last_name:
            raise ValidationError("First name and last name are required.")

        clean_email = email.strip().lower() if email else None
        if clean_email == "":
            clean_email = None

        old_values = {
            "first_name": member.first_name,
            "middle_name": member.middle_name,
            "last_name": member.last_name,
            "email": member.email,
            "ministry": member.ministry,
            "team": member.team,
            "department": member.department,
        }

        member.first_name = first_name
        member.middle_name = middle_name.strip() if middle_name else None
        member.last_name = last_name
        member.email = clean_email
        member.ministry = ministry.strip() if ministry else None
        member.team = team.strip() if team else None
        member.department = department.strip() if department else None
        member.save(update_fields=[
            "first_name", "middle_name", "last_name", "email",
            "ministry", "team", "department", "updated_at"
        ])

        AuditService.log(
            action="MEMBER_UPDATED",
            entity_type="Member",
            entity_id=member.id,
            user=actor,
            old_values=old_values,
            new_values={
                "first_name": member.first_name,
                "middle_name": member.middle_name,
                "last_name": member.last_name,
                "email": member.email,
                "ministry": member.ministry,
                "team": member.team,
                "department": member.department,
            },
            result=AuditResult.SUCCESS,
        )
        return member

    @classmethod
    def deactivate_member(cls, actor: User, member: Member, reason: str = "") -> Member:
        """
        Soft-deactivates a member without destroying historical contribution records.
        """
        if not actor.is_active or (not actor.is_system_administrator and not actor.has_permission("member.edit")):
            raise ValidationError("You do not have permission to deactivate members.")

        member.status = MemberStatus.DEACTIVATED
        member.save(update_fields=["status", "updated_at"])

        AuditService.log(
            action="MEMBER_DEACTIVATED",
            entity_type="Member",
            entity_id=member.id,
            user=actor,
            reason=reason or "Member deactivated",
            result=AuditResult.SUCCESS,
        )
        return member

    @classmethod
    def activate_member(cls, actor: User, member: Member) -> Member:
        """Restores a deactivated or inactive member to ACTIVE status."""
        if not actor.is_active or (not actor.is_system_administrator and not actor.has_permission("member.edit")):
            raise ValidationError("You do not have permission to activate members.")

        member.status = MemberStatus.ACTIVE
        member.save(update_fields=["status", "updated_at"])

        AuditService.log(
            action="MEMBER_ACTIVATED",
            entity_type="Member",
            entity_id=member.id,
            user=actor,
            result=AuditResult.SUCCESS,
        )
        return member

    @classmethod
    @transaction.atomic
    def add_phone_number(
        cls,
        actor: User,
        member: Member,
        phone_number: str,
        provider: str | None = None,
        is_primary: bool = False,
    ) -> MemberPhoneNumber:
        """
        Associates an additional phone number with a member.
        Enforces:
        - Active phone uniqueness across all members (PRD 5.2 / 5.7).
        - Single active primary phone per member.
        """
        if not actor.is_active or (not actor.is_system_administrator and not actor.has_permission("member.edit")):
            raise ValidationError("You do not have permission to manage member phone numbers.")

        clean_phone = phone_number.strip() if phone_number else ""
        if not clean_phone:
            raise ValidationError("Phone number cannot be blank.")

        # Check conflict with any other member's active phone
        conflict = MemberPhoneNumber.objects.filter(
            phone_number=clean_phone,
            is_active=True,
        ).exclude(member=member).first()
        if conflict:
            raise ValidationError(
                f"Phone number {clean_phone} is already actively assigned to member {conflict.member}."
            )

        # Check if member already has this exact number
        existing = MemberPhoneNumber.objects.filter(member=member, phone_number=clean_phone).first()
        if existing:
            if existing.is_active:
                raise ValidationError(f"This phone number is already actively associated with {member.full_name}.")
            # Reactivate existing record
            if is_primary:
                MemberPhoneNumber.objects.filter(member=member, is_active=True).update(is_primary=False)
            existing.is_active = True
            existing.is_primary = is_primary
            if provider:
                existing.provider = provider.strip()
            existing.save()
            return existing

        # If this is the member's first active number, automatically make it primary
        has_active_phones = MemberPhoneNumber.objects.filter(member=member, is_active=True).exists()
        if not has_active_phones:
            is_primary = True
        elif is_primary:
            # If requested as primary, clear primary from previous active phones
            MemberPhoneNumber.objects.filter(member=member, is_active=True).update(is_primary=False)

        phone = MemberPhoneNumber.objects.create(
            member=member,
            phone_number=clean_phone,
            provider=provider.strip() if provider else None,
            is_primary=is_primary,
            is_active=True,
        )

        AuditService.log(
            action="MEMBER_PHONE_ADDED",
            entity_type="MemberPhoneNumber",
            entity_id=phone.id,
            user=actor,
            new_values={
                "member_id": str(member.id),
                "phone_number": phone.phone_number,
                "is_primary": phone.is_primary,
            },
            result=AuditResult.SUCCESS,
        )
        return phone

    @classmethod
    @transaction.atomic
    def set_primary_phone(cls, actor: User, member: Member, phone_id: uuid.UUID) -> MemberPhoneNumber:
        """Promotes a member's active phone number to primary."""
        if not actor.is_active or (not actor.is_system_administrator and not actor.has_permission("member.edit")):
            raise ValidationError("You do not have permission to manage member phone numbers.")

        phone = MemberPhoneNumber.objects.filter(id=phone_id, member=member).first()
        if not phone:
            raise ValidationError("Phone number not found for this member.")

        if not phone.is_active:
            raise ValidationError("Cannot set an inactive phone number as primary.")

        MemberPhoneNumber.objects.filter(member=member, is_active=True).update(is_primary=False)
        phone.is_primary = True
        phone.save(update_fields=["is_primary", "updated_at"])

        AuditService.log(
            action="MEMBER_PRIMARY_PHONE_UPDATED",
            entity_type="MemberPhoneNumber",
            entity_id=phone.id,
            user=actor,
            new_values={"member_id": str(member.id), "phone_number": phone.phone_number},
            result=AuditResult.SUCCESS,
        )
        return phone

    @classmethod
    def toggle_phone_status(cls, actor: User, phone: MemberPhoneNumber, is_active: bool) -> MemberPhoneNumber:
        """Toggles a phone number between active and inactive."""
        if not actor.is_active or (not actor.is_system_administrator and not actor.has_permission("member.edit")):
            raise ValidationError("You do not have permission to manage member phone numbers.")

        if is_active:
            # Check for conflict before activating
            conflict = MemberPhoneNumber.objects.filter(
                phone_number=phone.phone_number,
                is_active=True,
            ).exclude(pk=phone.pk).first()
            if conflict:
                raise ValidationError(
                    f"Phone number {phone.phone_number} is already actively assigned to member {conflict.member}."
                )
            phone.is_active = True
        else:
            phone.is_active = False
            phone.is_primary = False

        phone.save(update_fields=["is_active", "is_primary", "updated_at"])

        AuditService.log(
            action="MEMBER_PHONE_STATUS_CHANGED",
            entity_type="MemberPhoneNumber",
            entity_id=phone.id,
            user=actor,
            new_values={"is_active": phone.is_active, "is_primary": phone.is_primary},
            result=AuditResult.SUCCESS,
        )
        return phone


class ContributorIdentificationService:
    """
    Core identity resolution engine for incoming contributions.
    Enforces PRD 5.4: Automatic contributions identify contributors STRICTLY by transaction phone number.
    Provider name, account reference, or name similarity MUST NEVER override this rule.
    """

    @classmethod
    def identify_contributor(
        cls,
        phone_number: str,
        provider: str = "",
        provider_name: str = "",
        provider_account_reference: str = "",
    ) -> tuple[Member | None, TemporaryContributor | None]:
        """
        Resolves contributor strictly by transaction phone number:
        1. Look up active phone in MemberPhoneNumber. If found, returns (member, None).
        2. If not found, looks up or creates TemporaryContributor. Returns (None, temporary_contributor).
        """
        clean_phone = phone_number.strip() if phone_number else ""
        if not clean_phone:
            raise ValidationError("A transaction phone number is required for contributor identification.")

        variants = get_phone_lookup_variants(clean_phone)

        # 1. Search active member phones using normalized equivalent variants
        active_phone = MemberPhoneNumber.objects.filter(
            phone_number__in=variants,
            is_active=True,
        ).select_related("member").first()

        if active_phone:
            # Member match found strictly by phone number (never by provider name)
            return active_phone.member, None

        # 2. No active member match: Look up or create Temporary Contributor
        tc = TemporaryContributor.objects.filter(
            phone_number__in=variants,
            status__in=[
                TemporaryContributorStatus.UNVERIFIED,
                TemporaryContributorStatus.UNDER_REVIEW,
            ]
        ).first()

        if not tc:
            tc = TemporaryContributor.objects.create(
                phone_number=clean_phone,
                provider=provider.strip() if provider else None,
                provider_name=provider_name.strip() if provider_name else None,
                provider_account_reference=provider_account_reference.strip() if provider_account_reference else None,
                status=TemporaryContributorStatus.UNVERIFIED,
            )
        elif provider_name and not tc.provider_name:
            tc.provider_name = provider_name.strip()
            tc.save(update_fields=["provider_name", "updated_at"])

        return None, tc


class TemporaryContributorService:
    """Service handling lifecycle, review, linking, and conversion of temporary contributors."""

    @classmethod
    def mark_under_review(cls, actor: User, temporary_contributor: TemporaryContributor) -> TemporaryContributor:
        """Places an unverified temporary contributor into review."""
        if not actor.is_active or (not actor.is_system_administrator and not actor.has_permission("member.edit")):
            raise ValidationError("You do not have permission to review temporary contributors.")

        temporary_contributor.status = TemporaryContributorStatus.UNDER_REVIEW
        temporary_contributor.save(update_fields=["status", "updated_at"])

        AuditService.log(
            action="TEMPORARY_CONTRIBUTOR_UNDER_REVIEW",
            entity_type="TemporaryContributor",
            entity_id=temporary_contributor.id,
            user=actor,
            result=AuditResult.SUCCESS,
        )
        return temporary_contributor

    @classmethod
    @transaction.atomic
    def request_linking(
        cls,
        actor: User,
        temporary_contributor: TemporaryContributor,
        target_member: Member,
        reason: str,
        add_phone: bool = True,
    ) -> ContributorLinkingRequest:
        """
        Submits a request to link an unverified temporary contributor to an existing church member.
        Ordinary authorized users submit requests; System Administrator approval is required.
        Checks and flags active phone number conflicts without silently rewriting or skipping records.
        Does NOT transfer contributions prior to approval.
        """
        if not actor.is_active or (not actor.is_system_administrator and not actor.has_permission("member.edit")):
            raise ValidationError("You do not have permission to request linking for temporary contributors.")

        if not reason or not reason.strip():
            raise ValidationError("A clear reason for linking is required.")

        # Lock temporary contributor to verify current state
        tc = TemporaryContributor.objects.select_for_update().get(id=temporary_contributor.id)
        if tc.status in [TemporaryContributorStatus.LINKED, TemporaryContributorStatus.CONVERTED]:
            raise ValidationError("This temporary contributor has already been linked or converted.")

        if target_member.status != MemberStatus.ACTIVE:
            raise ValidationError("Contributions can only be linked to active church members.")

        # Prevent duplicate pending linking request
        existing_pending = ContributorLinkingRequest.objects.filter(
            temporary_contributor=tc,
            status=ContributorLinkingStatus.PENDING,
        ).first()
        if existing_pending:
            raise ValidationError("A linking request for this contributor is already pending approval.")

        # Phone conflict check: check if contributor phone belongs to ANY other active member
        variants = get_phone_lookup_variants(tc.phone_number)
        conflict = MemberPhoneNumber.objects.filter(
            phone_number__in=variants,
            is_active=True,
        ).exclude(member=target_member).select_related("member").first()

        has_conflict = False
        conflict_details = ""
        if conflict:
            has_conflict = True
            conflict_details = (
                f"This phone number is already registered to another member: "
                f"{conflict.member.full_name} ({conflict.member.member_number})."
            )

        req = ContributorLinkingRequest.objects.create(
            temporary_contributor=tc,
            target_member=target_member,
            requested_by=actor,
            reason=reason.strip(),
            status=ContributorLinkingStatus.PENDING,
            has_phone_conflict=has_conflict,
            phone_conflict_details=conflict_details if has_conflict else "",
            add_phone_on_approval=add_phone,
        )

        AuditService.log(
            action="CONTRIBUTOR_LINKING_REQUESTED",
            entity_type="ContributorLinkingRequest",
            entity_id=req.id,
            user=actor,
            new_values={
                "temporary_contributor_id": str(tc.id),
                "target_member_id": str(target_member.id),
                "has_phone_conflict": has_conflict,
                "reason": reason.strip(),
            },
            result=AuditResult.SUCCESS,
        )
        return req

    @classmethod
    @transaction.atomic
    def approve_linking(
        cls,
        actor: User,
        linking_request: ContributorLinkingRequest,
        review_remarks: str = "",
    ) -> ContributorLinkingRequest:
        """
        Approves a contributor linking request.
        Restricted STRICTLY to System Administrators (PRD 5.6 & prompt).
        Transfers contributions atomically to target member without rewriting receipt numbers,
        contribution IDs, amounts, payment references or dispatching duplicate thank-you SMS.
        """
        if not actor.is_active or not actor.is_system_administrator:
            raise ValidationError("Only the System Administrator can approve contributor linking requests.")

        req = ContributorLinkingRequest.objects.select_for_update().get(id=linking_request.id)
        if req.status != ContributorLinkingStatus.PENDING:
            raise ValidationError("This linking request has already been reviewed.")

        tc = TemporaryContributor.objects.select_for_update().get(id=req.temporary_contributor_id)
        if tc.status in [TemporaryContributorStatus.LINKED, TemporaryContributorStatus.CONVERTED]:
            raise ValidationError("This contributor has already been linked or converted.")

        target_member = req.target_member
        reassigned_count = Contribution.objects.filter(
            temporary_contributor=tc
        ).update(
            member=target_member,
            temporary_contributor=None,
        )

        # Associate phone if requested and no conflict with another member
        if req.add_phone_on_approval and not req.has_phone_conflict:
            variants = get_phone_lookup_variants(tc.phone_number)
            conflict = MemberPhoneNumber.objects.filter(
                phone_number__in=variants,
                is_active=True,
            ).exclude(member=target_member).first()
            if not conflict:
                existing_on_target = MemberPhoneNumber.objects.filter(
                    member=target_member,
                    phone_number__in=variants,
                ).first()
                if not existing_on_target:
                    has_active = MemberPhoneNumber.objects.filter(member=target_member, is_active=True).exists()
                    MemberPhoneNumber.objects.create(
                        member=target_member,
                        phone_number=tc.phone_number,
                        provider=tc.provider,
                        is_primary=not has_active,
                        is_active=True,
                    )

        tc.status = TemporaryContributorStatus.LINKED
        tc.converted_member = target_member
        tc.save(update_fields=["status", "converted_member", "updated_at"])

        req.status = ContributorLinkingStatus.APPROVED
        req.reviewed_by = actor
        req.reviewed_at = timezone.now()
        req.review_remarks = review_remarks.strip() if review_remarks else ""
        req.save(update_fields=["status", "reviewed_by", "reviewed_at", "review_remarks", "updated_at"])

        AuditService.log(
            action="CONTRIBUTOR_LINKING_APPROVED",
            entity_type="ContributorLinkingRequest",
            entity_id=req.id,
            user=actor,
            new_values={
                "temporary_contributor_id": str(tc.id),
                "target_member_id": str(target_member.id),
                "reassigned_contributions_count": reassigned_count,
                "review_remarks": req.review_remarks,
            },
            result=AuditResult.SUCCESS,
        )
        return req

    @classmethod
    @transaction.atomic
    def reject_linking(
        cls,
        actor: User,
        linking_request: ContributorLinkingRequest,
        review_remarks: str = "",
    ) -> ContributorLinkingRequest:
        """
        Rejects a contributor linking request.
        Restricted STRICTLY to System Administrators.
        Does NOT transfer contributions; temporary contributor remains intact.
        """
        if not actor.is_active or not actor.is_system_administrator:
            raise ValidationError("Only the System Administrator can reject contributor linking requests.")

        req = ContributorLinkingRequest.objects.select_for_update().get(id=linking_request.id)
        if req.status != ContributorLinkingStatus.PENDING:
            raise ValidationError("This linking request has already been reviewed.")

        req.status = ContributorLinkingStatus.REJECTED
        req.reviewed_by = actor
        req.reviewed_at = timezone.now()
        req.review_remarks = review_remarks.strip() if review_remarks else ""
        req.save(update_fields=["status", "reviewed_by", "reviewed_at", "review_remarks", "updated_at"])

        AuditService.log(
            action="CONTRIBUTOR_LINKING_REJECTED",
            entity_type="ContributorLinkingRequest",
            entity_id=req.id,
            user=actor,
            new_values={
                "temporary_contributor_id": str(req.temporary_contributor_id),
                "target_member_id": str(req.target_member_id),
                "reason": req.reason,
                "review_remarks": req.review_remarks,
            },
            result=AuditResult.SUCCESS,
        )
        return req

    @classmethod
    @transaction.atomic
    def link_to_member(
        cls,
        actor: User,
        temporary_contributor: TemporaryContributor,
        target_member: Member,
        add_phone: bool = True,
    ) -> TemporaryContributor:
        """
        Links an unverified temporary contributor to an existing permanent member.
        Reassigns all contributions and optionally associates the phone number.
        Preserves backward compatibility while enforcing concurrency locking and audit trails.
        """
        if not actor.is_active or (not actor.is_system_administrator and not actor.has_permission("member.edit")):
            raise ValidationError("You do not have permission to link temporary contributors.")

        tc = TemporaryContributor.objects.select_for_update().get(id=temporary_contributor.id)
        if tc.status in [TemporaryContributorStatus.LINKED, TemporaryContributorStatus.CONVERTED]:
            raise ValidationError("This temporary contributor has already been linked or converted.")

        # Reassign historical contributions
        reassigned_count = Contribution.objects.filter(
            temporary_contributor=tc
        ).update(
            member=target_member,
            temporary_contributor=None,
        )

        # Optionally add the phone number to target member if not conflicting
        if add_phone:
            variants = get_phone_lookup_variants(tc.phone_number)
            conflict = MemberPhoneNumber.objects.filter(
                phone_number__in=variants,
                is_active=True,
            ).exclude(member=target_member).first()
            if not conflict:
                existing_member_phone = MemberPhoneNumber.objects.filter(
                    member=target_member,
                    phone_number__in=variants,
                ).first()
                if not existing_member_phone:
                    has_active = MemberPhoneNumber.objects.filter(member=target_member, is_active=True).exists()
                    MemberPhoneNumber.objects.create(
                        member=target_member,
                        phone_number=tc.phone_number,
                        provider=tc.provider,
                        is_primary=not has_active,
                        is_active=True,
                    )

        tc.status = TemporaryContributorStatus.LINKED
        tc.converted_member = target_member
        tc.save(update_fields=["status", "converted_member", "updated_at"])

        AuditService.log(
            action="TEMPORARY_CONTRIBUTOR_LINKED",
            entity_type="TemporaryContributor",
            entity_id=tc.id,
            user=actor,
            new_values={
                "target_member_id": str(target_member.id),
                "reassigned_contributions_count": reassigned_count,
            },
            result=AuditResult.SUCCESS,
        )
        return tc

    @classmethod
    @transaction.atomic
    def convert_to_member(
        cls,
        actor: User,
        temporary_contributor: TemporaryContributor,
        first_name: str,
        last_name: str,
        middle_name: str = "",
        email: str = "",
        ministry: str = "",
        team: str = "",
        department: str = "",
    ) -> Member:
        """
        Converts a temporary contributor into a new permanent member.
        Reassigns all contributions to the newly created member.
        Enforces:
        - select_for_update locking against concurrent requests
        - check normalized phone numbers for existing membership before creating member
        - client-facing phone conflict messaging
        - preserves original temporary record, receipts, and audit history
        """
        if not actor.is_active or (not actor.is_system_administrator and not actor.has_permission("member.create")):
            raise ValidationError("You do not have permission to convert temporary contributors.")

        tc = TemporaryContributor.objects.select_for_update().get(id=temporary_contributor.id)
        if tc.status in [TemporaryContributorStatus.CONVERTED, TemporaryContributorStatus.LINKED]:
            raise ValidationError("This temporary contributor has already been registered or linked.")

        # Check normalized phone numbers for existing active membership
        variants = get_phone_lookup_variants(tc.phone_number)
        phone_conflict = MemberPhoneNumber.objects.filter(
            phone_number__in=variants,
            is_active=True,
        ).first()
        if phone_conflict:
            raise ValidationError(
                "This phone number is already registered to another member. Please review the existing member."
            )

        new_member = MemberService.create_member(
            creator=actor,
            first_name=first_name,
            last_name=last_name,
            middle_name=middle_name,
            email=email,
            ministry=ministry,
            team=team,
            department=department,
            phone_number=tc.phone_number,
            phone_provider=tc.provider,
            is_primary_phone=True,
        )

        reassigned_count = Contribution.objects.filter(
            temporary_contributor=tc
        ).update(
            member=new_member,
            temporary_contributor=None,
        )

        tc.status = TemporaryContributorStatus.CONVERTED
        tc.converted_member = new_member
        tc.save(update_fields=["status", "converted_member", "updated_at"])

        AuditService.log(
            action="TEMPORARY_CONTRIBUTOR_CONVERTED",
            entity_type="TemporaryContributor",
            entity_id=tc.id,
            user=actor,
            new_values={
                "new_member_id": str(new_member.id),
                "new_member_number": new_member.member_number,
                "reassigned_contributions_count": reassigned_count,
            },
            result=AuditResult.SUCCESS,
        )
        return new_member


class MemberMergeService:
    """
    Service governing member linking and merging workflows.
    Enforces PRD 5.6 & Spec Section 7: Merge approval/rejection is STRICTLY restricted to the System Administrator.
    """

    @classmethod
    def request_merge(
        cls,
        requester: User,
        source_member: Member,
        target_member: Member,
        reason: str,
    ) -> MemberMergeRequest:
        """
        Initiates a member merge request.
        Open to users with 'member.merge.request' permission or the System Administrator.
        """
        if not requester.is_active or (not requester.is_system_administrator and not requester.has_permission("member.merge.request")):
            raise ValidationError("You do not have permission to request member merges.")

        if source_member == target_member:
            raise ValidationError("Source member and target member cannot be identical.")

        reason = reason.strip() if reason else ""
        if not reason:
            raise ValidationError("A reason must be provided for requesting a member merge.")

        existing_pending = MemberMergeRequest.objects.filter(
            source_member=source_member,
            status=MemberMergeStatus.PENDING,
        ).exists()
        if existing_pending:
            raise ValidationError(f"A pending merge request already exists for source member {source_member.member_number}.")

        merge_request = MemberMergeRequest.objects.create(
            source_member=source_member,
            target_member=target_member,
            requested_by=requester,
            reason=reason,
            status=MemberMergeStatus.PENDING,
        )

        AuditService.log(
            action="MEMBER_MERGE_REQUESTED",
            entity_type="MemberMergeRequest",
            entity_id=merge_request.id,
            user=requester,
            new_values={
                "source_member": source_member.member_number,
                "target_member": target_member.member_number,
                "reason": reason,
            },
            result=AuditResult.SUCCESS,
        )
        return merge_request

    @classmethod
    @transaction.atomic
    def review_merge(
        cls,
        reviewer: User,
        merge_request: MemberMergeRequest,
        approve: bool,
        remarks: str = "",
    ) -> MemberMergeRequest:
        """
        Reviews and executes or rejects a member merge request.
        STRICT BOUNDARY: Only the System Administrator may approve or reject a merge request.
        """
        if not reviewer.is_active or not reviewer.is_system_administrator:
            raise ValidationError("Only the System Administrator may approve or reject member merge requests.")

        if merge_request.status != MemberMergeStatus.PENDING:
            raise ValidationError(f"This merge request has already been reviewed ({merge_request.status}).")

        merge_request.reviewed_by = reviewer
        merge_request.reviewed_at = timezone.now()
        merge_request.review_remarks = remarks.strip() if remarks else None

        if approve:
            source = merge_request.source_member
            target = merge_request.target_member

            # 1. Reassign all contributions
            reassigned_count = Contribution.objects.filter(member=source).update(member=target)

            # 2. Reassign active phone numbers (handling duplicates)
            target_active_phones = set(
                MemberPhoneNumber.objects.filter(member=target, is_active=True).values_list("phone_number", flat=True)
            )
            for phone in source.phone_numbers.filter(is_active=True):
                if phone.phone_number in target_active_phones:
                    phone.is_active = False
                    phone.is_primary = False
                    phone.save(update_fields=["is_active", "is_primary", "updated_at"])
                else:
                    phone.member = target
                    phone.is_primary = False  # Keep target's existing primary phone intact
                    phone.save(update_fields=["member", "is_primary", "updated_at"])

            # 3. Soft-deactivate source member
            source.status = MemberStatus.DEACTIVATED
            source.save(update_fields=["status", "updated_at"])

            AuditService.log(
                action="MEMBER_DEACTIVATED",
                entity_type="Member",
                entity_id=source.id,
                user=reviewer,
                reason=f"Merged into member {target.member_number}",
                result=AuditResult.SUCCESS,
            )

            merge_request.status = MemberMergeStatus.APPROVED
            merge_request.save(update_fields=["status", "reviewed_by", "reviewed_at", "review_remarks", "updated_at"])

            AuditService.log(
                action="MEMBER_MERGE_APPROVED",
                entity_type="MemberMergeRequest",
                entity_id=merge_request.id,
                user=reviewer,
                new_values={
                    "source_member": source.member_number,
                    "target_member": target.member_number,
                    "reassigned_contributions": reassigned_count,
                    "remarks": merge_request.review_remarks,
                },
                result=AuditResult.SUCCESS,
            )
        else:
            merge_request.status = MemberMergeStatus.REJECTED
            merge_request.save(update_fields=["status", "reviewed_by", "reviewed_at", "review_remarks", "updated_at"])

            AuditService.log(
                action="MEMBER_MERGE_REJECTED",
                entity_type="MemberMergeRequest",
                entity_id=merge_request.id,
                user=reviewer,
                reason=merge_request.review_remarks or "Merge rejected by administrator",
                result=AuditResult.SUCCESS,
            )

        return merge_request


class MemberImportService:
    """
    Service managing bulk Excel member importation.
    Provides downloadable templates, row-by-row conflict detection, phone normalization,
    and atomic member creation via existing MemberService invariants.
    """

    @classmethod
    def generate_template(cls) -> bytes:
        import io
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

        wb = openpyxl.Workbook()

        # Sheet 1: Data Sheet
        ws = wb.active
        ws.title = "Members"

        headers = [
            "First Name *",
            "Middle Name",
            "Last Name *",
            "Phone Number *",
            "Email",
            "Ministry",
            "Team",
            "Department",
        ]
        ws.append(headers)

        # Style Header
        navy_fill = PatternFill(start_color="011345", end_color="011345", fill_type="solid")
        header_font = Font(name="Cabin", size=11, bold=True, color="FFFFFF")
        thin_border = Border(
            left=Side(style="thin", color="CCCCCC"),
            right=Side(style="thin", color="CCCCCC"),
            top=Side(style="thin", color="CCCCCC"),
            bottom=Side(style="thin", color="CCCCCC"),
        )

        for col_num in range(1, len(headers) + 1):
            cell = ws.cell(row=1, column=col_num)
            cell.fill = navy_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal="center", vertical="center")
            cell.border = thin_border

        # Example rows
        examples = [
            ["Kwame", "", "Mensah", "0241234567", "kwame.mensah@example.com", "Choir", "Youth", "Worship"],
            ["Ama", "Akua", "Osei", "+233209876543", "ama.osei@example.com", "Ushering", "Protocol", "Evangelism"],
        ]
        for r_idx, row_data in enumerate(examples, start=2):
            for c_idx, val in enumerate(row_data, start=1):
                cell = ws.cell(row=r_idx, column=c_idx)
                cell.value = str(val)
                cell.number_format = "@"  # Text format to preserve leading zeroes!
                cell.font = Font(name="Cabin", size=10)
                cell.border = thin_border

        # Set column widths
        col_widths = [18, 16, 18, 20, 28, 16, 16, 16]
        for col_idx, width in enumerate(col_widths, start=1):
            col_letter = openpyxl.utils.get_column_letter(col_idx)
            ws.column_dimensions[col_letter].width = width

        # Sheet 2: Instructions Sheet
        ws_info = wb.create_sheet(title="Instructions")
        ws_info.append(["TCI Higher Life Center — Member Excel Import Instructions"])
        ws_info.append([])
        ws_info.append(["Field Name", "Requirement", "Accepted Format", "Notes"])

        field_info = [
            ["First Name", "Required", "Plain text (e.g. Kwame)", "Primary given name"],
            ["Middle Name", "Optional", "Plain text (e.g. Akua)", "Secondary name if applicable"],
            ["Last Name", "Required", "Plain text (e.g. Mensah)", "Family or surname"],
            ["Phone Number", "Required", "Text format: 024XXXXXXX or +23324XXXXXXX", "Must be formatted as Text in Excel to preserve leading 0. Unique per active member."],
            ["Email", "Optional", "Valid email (e.g. name@example.com)", "Contact email"],
            ["Ministry", "Optional", "Plain text (e.g. Choir, Ushering)", "Church ministry"],
            ["Team", "Optional", "Plain text (e.g. Youth, Men of Valour)", "Fellowship team"],
            ["Department", "Optional", "Plain text (e.g. Protocol, Media)", "Operational department"],
        ]
        for row in field_info:
            ws_info.append(row)

        ws_info.append([])
        ws_info.append(["Important Guidelines:"])
        ws_info.append(["1. Phone numbers must be unique across all church members."])
        ws_info.append(["2. Re-uploading existing members will be flagged and not duplicated."])
        ws_info.append(["3. Spreadsheet formulas are disabled for security."])

        # Style instructions
        ws_info["A1"].font = Font(name="Cabin", size=14, bold=True, color="011345")
        for col_idx in range(1, 5):
            c = ws_info.cell(row=3, column=col_idx)
            c.fill = PatternFill(start_color="F16822", end_color="F16822", fill_type="solid")
            c.font = Font(name="Cabin", size=11, bold=True, color="FFFFFF")

        ws_info.column_dimensions["A"].width = 20
        ws_info.column_dimensions["B"].width = 16
        ws_info.column_dimensions["C"].width = 40
        ws_info.column_dimensions["D"].width = 50

        stream = io.BytesIO()
        wb.save(stream)
        return stream.getvalue()

    @classmethod
    def parse_and_validate_file(cls, file_obj, actor: User) -> dict:
        import openpyxl
        from apps.core.utils.phone import validate_phone_number

        if not actor.is_system_administrator and not actor.has_permission("member.create"):
            raise PermissionDenied("You do not have permission to import members.")

        try:
            file_bytes = file_obj.read()
            file_hash = hashlib.sha256(file_bytes).hexdigest()
            file_obj.seek(0)
            wb = openpyxl.load_workbook(file_obj, data_only=True)
        except Exception as e:
            raise ValidationError(f"Unable to read Excel file: {str(e)}")

        ws = wb.active
        rows_iter = ws.iter_rows(values_only=True)
        try:
            raw_headers = next(rows_iter)
        except StopIteration:
            raise ValidationError("The uploaded Excel file is empty.")

        if not raw_headers:
            raise ValidationError("The uploaded Excel file does not contain a header row.")

        # Map header positions
        col_map = {}
        for idx, h in enumerate(raw_headers):
            if h is None:
                continue
            h_clean = str(h).strip().lower().replace("*", "").strip()
            if "first" in h_clean:
                col_map["first_name"] = idx
            elif "middle" in h_clean:
                col_map["middle_name"] = idx
            elif "last" in h_clean:
                col_map["last_name"] = idx
            elif "phone" in h_clean:
                col_map["phone_number"] = idx
            elif "email" in h_clean:
                col_map["email"] = idx
            elif "ministry" in h_clean:
                col_map["ministry"] = idx
            elif "team" in h_clean:
                col_map["team"] = idx
            elif "dept" in h_clean or "department" in h_clean:
                col_map["department"] = idx

        if "first_name" not in col_map or "last_name" not in col_map:
            raise ValidationError(
                "Missing required columns in Excel header. 'First Name' and 'Last Name' are required."
            )

        seen_phones_in_file: dict[str, int] = {}
        parsed_rows = []
        valid_count = 0
        conflict_count = 0
        review_required_count = 0
        error_count = 0

        for row_num, row_values in enumerate(rows_iter, start=2):
            if not row_values or all(v is None or str(v).strip() == "" for v in row_values):
                continue  # skip completely blank rows

            def get_val(key):
                idx = col_map.get(key)
                if idx is not None and idx < len(row_values):
                    val = row_values[idx]
                    if val is not None:
                        s = str(val).strip()
                        # Reject or sanitize formula injections
                        if s.startswith("="):
                            s = s[1:].strip()
                        return s
                return ""

            first_name = get_val("first_name")
            middle_name = get_val("middle_name")
            last_name = get_val("last_name")
            phone_raw = get_val("phone_number")
            email = get_val("email")
            ministry = get_val("ministry")
            team = get_val("team")
            department = get_val("department")

            row_errors = []
            status = "READY"
            matched_member_info = None

            # 1. Name validation
            if not first_name or not last_name:
                row_errors.append("First name and last name are required.")
                status = "ERROR"

            # 2. Phone validation
            clean_phone = clean_phone_number(phone_raw) if phone_raw else ""
            if phone_raw and not clean_phone:
                row_errors.append("Invalid phone number format.")
                status = "ERROR"
            elif clean_phone:
                if not validate_phone_number(clean_phone):
                    row_errors.append(f"Phone number '{phone_raw}' is not a valid phone number (expected 9-15 digits).")
                    status = "ERROR"
                else:
                    # In-file duplicate check
                    if clean_phone in seen_phones_in_file:
                        prev_row = seen_phones_in_file[clean_phone]
                        row_errors.append(f"Duplicate phone number in file (already in row {prev_row}).")
                        status = "CONFLICT"
                    else:
                        seen_phones_in_file[clean_phone] = row_num

                    # Database conflict check
                    variants = get_phone_lookup_variants(clean_phone)
                    existing_phone = MemberPhoneNumber.objects.filter(
                        phone_number__in=variants,
                        is_active=True,
                    ).select_related("member").first()

                    if existing_phone:
                        m = existing_phone.member
                        matched_member_info = f"{m.full_name} ({m.member_number})"
                        row_errors.append(f"This phone number is already registered to another member ({matched_member_info}).")
                        status = "CONFLICT"
            else:
                # No phone number provided
                # PRD rule: Flag for staff review before creating potentially duplicate members.
                # Do NOT identify or merge people solely by name.
                if status != "ERROR":
                    existing_m = Member.objects.filter(
                        first_name__iexact=first_name,
                        last_name__iexact=last_name,
                        status=MemberStatus.ACTIVE,
                    ).first()
                    if existing_m:
                        matched_member_info = f"{existing_m.full_name} ({existing_m.member_number})"
                        row_errors.append(
                            f"No phone number provided and a member with this name already exists ({matched_member_info}). "
                            f"Requires staff review to prevent creating duplicate members."
                        )
                        status = "REVIEW_REQUIRED"
                    else:
                        row_errors.append(
                            "No phone number provided. Requires staff review before creating a member record without phone contact."
                        )
                        status = "REVIEW_REQUIRED"

            if status == "READY":
                valid_count += 1
            elif status == "REVIEW_REQUIRED":
                review_required_count += 1
            elif status == "CONFLICT":
                conflict_count += 1
            else:
                error_count += 1

            parsed_rows.append({
                "row_number": row_num,
                "first_name": first_name,
                "middle_name": middle_name,
                "last_name": last_name,
                "phone_number": clean_phone or phone_raw,
                "email": email,
                "ministry": ministry,
                "team": team,
                "department": department,
                "status": status,
                "errors": row_errors,
                "matched_member": matched_member_info,
            })

        return {
            "file_hash": file_hash,
            "total_rows": len(parsed_rows),
            "valid_count": valid_count,
            "conflict_count": conflict_count,
            "review_required_count": review_required_count,
            "error_count": error_count,
            "rows": parsed_rows,
        }

    @classmethod
    def create_import_batch(
        cls,
        actor: User,
        file_hash: str,
        filename: str,
        preview_data: dict,
    ) -> MemberImportBatch:
        if not actor.is_system_administrator and not actor.has_permission("member.create"):
            raise PermissionDenied("You do not have permission to import members.")

        token = uuid.uuid4().hex
        batch = MemberImportBatch.objects.create(
            import_token=token,
            file_hash=file_hash,
            filename=filename,
            created_by=actor,
            status=MemberImportStatus.PENDING,
            total_rows=preview_data.get("total_rows", 0),
            valid_count=preview_data.get("valid_count", 0),
            conflict_count=preview_data.get("conflict_count", 0),
            review_required_count=preview_data.get("review_required_count", 0),
            error_count=preview_data.get("error_count", 0),
            row_data=preview_data.get("rows", []),
        )
        return batch

    @classmethod
    @transaction.atomic
    def execute_import(
        cls,
        actor: User,
        valid_rows: list[dict] | None = None,
        import_token: str | None = None,
        confirm_phoneless: bool = False,
    ) -> dict:
        if not actor.is_system_administrator and not actor.has_permission("member.create"):
            raise PermissionDenied("You do not have permission to import members.")

        token = import_token
        batch = None
        if token:
            batch = MemberImportBatch.objects.select_for_update().filter(import_token=token).first()

        if not batch:
            if valid_rows is not None:
                token = token or uuid.uuid4().hex
                batch = MemberImportBatch.objects.create(
                    import_token=token,
                    file_hash=uuid.uuid4().hex,
                    filename="programmatic_import.xlsx",
                    created_by=actor,
                    status=MemberImportStatus.PENDING,
                    total_rows=len(valid_rows),
                    valid_count=len(valid_rows),
                    row_data=valid_rows,
                )
                batch = MemberImportBatch.objects.select_for_update().filter(id=batch.id).first()
            else:
                raise ValidationError("Invalid, altered, or expired import token.")

        if batch.created_by != actor and not actor.is_system_administrator:
            raise PermissionDenied("You do not have permission to confirm this member import.")

        if batch.status == MemberImportStatus.COMPLETED:
            member_ids = batch.result_summary.get("member_ids", [])
            members = list(Member.objects.filter(id__in=member_ids))
            return {
                "batch": batch,
                "imported_count": len(members),
                "skipped_count": batch.result_summary.get("skipped_count", 0),
                "created_members": members,
                "is_replay": True,
            }

        if batch.status == MemberImportStatus.PROCESSING:
            raise ValidationError("This member import is currently being processed by another request. Please wait.")

        batch.status = MemberImportStatus.PROCESSING
        batch.save(update_fields=["status"])

        rows_to_process = batch.row_data or valid_rows or []
        created_members = []
        skipped_count = 0

        for r in rows_to_process:
            st = r.get("status")
            if st == "READY" or (st == "REVIEW_REQUIRED" and confirm_phoneless):
                pass
            else:
                skipped_count += 1
                continue

            phone = r.get("phone_number", "").strip()
            # Double check concurrency phone conflict under lock
            if phone:
                variants = get_phone_lookup_variants(phone)
                conflict = MemberPhoneNumber.objects.filter(
                    phone_number__in=variants,
                    is_active=True,
                ).exists()
                if conflict:
                    skipped_count += 1
                    continue

            new_m = MemberService.create_member(
                creator=actor,
                first_name=r.get("first_name", ""),
                last_name=r.get("last_name", ""),
                middle_name=r.get("middle_name", ""),
                email=r.get("email", ""),
                ministry=r.get("ministry", ""),
                team=r.get("team", ""),
                department=r.get("department", ""),
                phone_number=phone or None,
            )
            created_members.append(new_m)

        batch.status = MemberImportStatus.COMPLETED
        batch.completed_at = timezone.now()
        batch.result_summary = {
            "imported_count": len(created_members),
            "skipped_count": skipped_count,
            "member_ids": [str(m.id) for m in created_members],
        }
        batch.save(update_fields=["status", "completed_at", "result_summary"])

        AuditService.log(
            action="MEMBERS_EXCEL_IMPORTED",
            entity_type="MemberImportBatch",
            entity_id=batch.id,
            user=actor,
            new_values={
                "import_token": batch.import_token,
                "imported_count": len(created_members),
                "skipped_conflicts": skipped_count,
                "created_member_numbers": [m.member_number for m in created_members[:50]],
            },
            result=AuditResult.SUCCESS,
        )

        return {
            "batch": batch,
            "imported_count": len(created_members),
            "skipped_count": skipped_count,
            "created_members": created_members,
            "is_replay": False,
        }
