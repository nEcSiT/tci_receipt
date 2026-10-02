import logging
import uuid
from django.db import transaction
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.accounts.models import User
from apps.audit.models import AuditResult
from apps.audit.services import AuditService
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

        # 1. Search active member phones
        active_phone = MemberPhoneNumber.objects.filter(
            phone_number=clean_phone,
            is_active=True,
        ).select_related("member").first()

        if active_phone:
            # Member match found
            return active_phone.member, None

        # 2. No active member match: Look up or create Temporary Contributor
        tc = TemporaryContributor.objects.filter(
            phone_number=clean_phone,
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
        """
        if not actor.is_active or (not actor.is_system_administrator and not actor.has_permission("member.edit")):
            raise ValidationError("You do not have permission to link temporary contributors.")

        # Reassign historical contributions
        reassigned_count = Contribution.objects.filter(
            temporary_contributor=temporary_contributor
        ).update(
            member=target_member,
            temporary_contributor=None,
        )

        # Optionally add the phone number to target member if not conflicting
        if add_phone:
            conflict = MemberPhoneNumber.objects.filter(
                phone_number=temporary_contributor.phone_number,
                is_active=True,
            ).exclude(member=target_member).first()
            if not conflict:
                existing_member_phone = MemberPhoneNumber.objects.filter(
                    member=target_member,
                    phone_number=temporary_contributor.phone_number,
                ).first()
                if not existing_member_phone:
                    has_active = MemberPhoneNumber.objects.filter(member=target_member, is_active=True).exists()
                    MemberPhoneNumber.objects.create(
                        member=target_member,
                        phone_number=temporary_contributor.phone_number,
                        provider=temporary_contributor.provider,
                        is_primary=not has_active,
                        is_active=True,
                    )

        temporary_contributor.status = TemporaryContributorStatus.LINKED
        temporary_contributor.converted_member = target_member
        temporary_contributor.save(update_fields=["status", "converted_member", "updated_at"])

        AuditService.log(
            action="TEMPORARY_CONTRIBUTOR_LINKED",
            entity_type="TemporaryContributor",
            entity_id=temporary_contributor.id,
            user=actor,
            new_values={
                "target_member_id": str(target_member.id),
                "reassigned_contributions_count": reassigned_count,
            },
            result=AuditResult.SUCCESS,
        )
        return temporary_contributor

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
        """
        if not actor.is_active or (not actor.is_system_administrator and not actor.has_permission("member.create")):
            raise ValidationError("You do not have permission to convert temporary contributors.")

        new_member = MemberService.create_member(
            creator=actor,
            first_name=first_name,
            last_name=last_name,
            middle_name=middle_name,
            email=email,
            ministry=ministry,
            team=team,
            department=department,
            phone_number=temporary_contributor.phone_number,
            phone_provider=temporary_contributor.provider,
            is_primary_phone=True,
        )

        reassigned_count = Contribution.objects.filter(
            temporary_contributor=temporary_contributor
        ).update(
            member=new_member,
            temporary_contributor=None,
        )

        temporary_contributor.status = TemporaryContributorStatus.CONVERTED
        temporary_contributor.converted_member = new_member
        temporary_contributor.save(update_fields=["status", "converted_member", "updated_at"])

        AuditService.log(
            action="TEMPORARY_CONTRIBUTOR_CONVERTED",
            entity_type="TemporaryContributor",
            entity_id=temporary_contributor.id,
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
