import datetime
import logging
from decimal import Decimal, InvalidOperation
from django.db import transaction
from django.core.exceptions import ValidationError, PermissionDenied
from django.db.models import Q
from django.utils import timezone

from apps.accounts.models import User
from apps.audit.models import AuditResult
from apps.audit.services import AuditService
from apps.contributions.models import (
    Contribution,
    ContributionType,
    PaymentTransaction,
    PaymentMode,
    EntryMethod,
    ContributionStatus,
    PaymentTransactionStatus,
)
from apps.members.models import Member, TemporaryContributor, MemberStatus
from apps.members.services import ContributorIdentificationService

logger = logging.getLogger(__name__)


class ContributionService:
    """
    Core business service managing Church Contribution lifecycle, manual recording,
    automatic contribution transaction processing, duplicate protection, and query/search operations.
    """

    @classmethod
    @transaction.atomic
    def record_manual_contribution(
        cls,
        recorder: User,
        member: Member,
        contribution_type: ContributionType,
        amount: Decimal | str | float,
        payment_mode: str,
        reference_number: str | None = None,
        custom_type_description: str | None = None,
        contribution_date: datetime.date | str | None = None,
        currency: str = "GHS",
    ) -> Contribution:
        """
        Records a validated manual church contribution against an existing active Member.
        Enforces strict role permissions, amount validation, type description requirements,
        and append-only financial audit trails.
        """
        # 1. Permission verification
        if not recorder.is_active:
            raise PermissionDenied("User account is inactive.")
        if not recorder.is_system_administrator and not recorder.has_permission("contribution.create"):
            raise PermissionDenied("You do not have permission to record contributions.")

        # 2. Member validation
        if not member:
            raise ValidationError("A valid member must be selected for manual contributions.")
        if member.status == MemberStatus.DEACTIVATED:
            raise ValidationError(f"Cannot record contributions against deactivated member {member.full_name}.")

        # 3. Amount validation
        if amount is None or amount == "":
            raise ValidationError("Contribution amount is required.")
        try:
            parsed_amount = Decimal(str(amount)).quantize(Decimal("0.01"))
        except (InvalidOperation, TypeError, ValueError):
            raise ValidationError("Contribution amount must be a valid numeric monetary value.")

        if parsed_amount <= Decimal("0.00"):
            raise ValidationError("Contribution amount must be greater than zero.")

        # 4. Contribution Type validation
        if not contribution_type or not contribution_type.is_active:
            raise ValidationError("A valid and active contribution type must be selected.")

        clean_description = custom_type_description.strip() if custom_type_description else None
        if contribution_type.name.strip().lower() == "other":
            if not clean_description:
                raise ValidationError("Description is required when contribution type is 'Other'.")

        # 5. Payment Mode validation
        if payment_mode not in PaymentMode.values:
            raise ValidationError(f"Invalid payment mode '{payment_mode}'.")

        clean_reference = reference_number.strip() if reference_number else None

        # 6. Contribution Date validation
        parsed_date = None
        if contribution_date:
            if isinstance(contribution_date, datetime.date):
                parsed_date = contribution_date
            elif isinstance(contribution_date, str):
                try:
                    parsed_date = datetime.date.fromisoformat(contribution_date.strip())
                except ValueError:
                    raise ValidationError("Invalid contribution date format. Use YYYY-MM-DD.")
        if not parsed_date:
            parsed_date = timezone.localdate()

        # 7. Record Creation
        contribution = Contribution.objects.create(
            member=member,
            temporary_contributor=None,
            payment_transaction=None,
            contribution_type=contribution_type,
            custom_type_description=clean_description,
            amount=parsed_amount,
            currency=currency,
            payment_mode=payment_mode,
            entry_method=EntryMethod.MANUAL,
            reference_number=clean_reference,
            contribution_date=parsed_date,
            status=ContributionStatus.CONFIRMED,
            recorded_by=recorder,
        )

        # 8. Immutable Audit Trail Emission
        AuditService.log(
            action="CONTRIBUTION_CREATED",
            entity_type="Contribution",
            entity_id=contribution.id,
            user=recorder,
            new_values={
                "contribution_number": contribution.contribution_number,
                "member_id": str(member.id),
                "member_number": member.member_number,
                "member_name": member.full_name,
                "contribution_type": contribution_type.name,
                "custom_type_description": clean_description,
                "amount": str(parsed_amount),
                "currency": currency,
                "payment_mode": payment_mode,
                "entry_method": EntryMethod.MANUAL,
                "reference_number": clean_reference,
                "contribution_date": str(parsed_date),
                "status": ContributionStatus.CONFIRMED,
            },
            result=AuditResult.SUCCESS,
        )

        logger.info(
            "Manual contribution %s (GHS %s, %s) recorded by %s for member %s",
            contribution.contribution_number,
            parsed_amount,
            contribution_type.name,
            recorder.email,
            member.member_number,
        )
        return contribution

    @classmethod
    def record_automatic_contribution(
        cls,
        payment_transaction: PaymentTransaction,
        contribution_type: ContributionType,
        amount: Decimal | str | float | None = None,
        member: Member | None = None,
        temporary_contributor: TemporaryContributor | None = None,
        custom_type_description: str | None = None,
        contribution_date: datetime.date | str | None = None,
        currency: str = "GHS",
        actor: User | None = None,
    ) -> Contribution:
        """
        Records an automatic contribution derived from an inbound payment transaction.
        Enforces:
        1. Duplicate protection rule: Provider Reference Number + Contribution Type.
        2. Flagging for review if the same provider reference appears with a different contribution type.
        3. Strict phone-number-based contributor identification (M3 rule) if contributor not provided.
        """
        if not payment_transaction:
            raise ValidationError("Automatic contributions require an associated payment transaction.")

        if not contribution_type or not contribution_type.is_active:
            raise ValidationError("A valid active contribution type is required.")

        # --- DUPLICATE PROTECTION RULE ---
        # Rule: Provider Reference Number + Contribution Type
        existing_tx_contributions = Contribution.objects.filter(
            payment_transaction__provider_reference=payment_transaction.provider_reference
        ).select_related("contribution_type")

        # Check for Exact Duplicate
        exact_duplicate = existing_tx_contributions.filter(contribution_type=contribution_type).first()
        if exact_duplicate:
            # Mark transaction as duplicate and prevent second financial record
            payment_transaction.status = PaymentTransactionStatus.DUPLICATE
            payment_transaction.failure_reason = (
                f"Duplicate transaction: Provider reference '{payment_transaction.provider_reference}' "
                f"with contribution type '{contribution_type.name}' has already been recorded."
            )
            payment_transaction.save(update_fields=["status", "failure_reason", "updated_at"])

            AuditService.log(
                action="DUPLICATE_CONTRIBUTION_BLOCKED",
                entity_type="PaymentTransaction",
                entity_id=payment_transaction.id,
                user=actor,
                new_values={
                    "provider_reference": payment_transaction.provider_reference,
                    "contribution_type": contribution_type.name,
                    "existing_contribution_number": exact_duplicate.contribution_number,
                    "reason": "Exact duplicate: Provider Reference Number + Contribution Type already exists",
                },
                result=AuditResult.FAILURE,
            )
            raise ValidationError(
                f"Duplicate contribution detected: Provider reference '{payment_transaction.provider_reference}' "
                f"with contribution type '{contribution_type.name}' already recorded."
            )

        # Check for Same Provider Reference with Different Contribution Type -> Flag for Review
        flagged_for_review = False
        if existing_tx_contributions.exists():
            flagged_for_review = True
            AuditService.log(
                action="CONTRIBUTION_FLAGGED_FOR_REVIEW",
                entity_type="PaymentTransaction",
                entity_id=payment_transaction.id,
                user=actor,
                new_values={
                    "provider_reference": payment_transaction.provider_reference,
                    "existing_types": [c.contribution_type.name for c in existing_tx_contributions],
                    "new_type": contribution_type.name,
                    "reason": "Same provider reference appears with a different contribution type; flagged for review.",
                },
                result=AuditResult.PARTIAL,
            )

        # Contributor resolution: strictly by transaction phone number if not explicitly specified
        if not member and not temporary_contributor:
            matched_member, tc = ContributorIdentificationService.identify_contributor(
                phone_number=payment_transaction.transaction_phone,
                provider=payment_transaction.provider,
                provider_name=payment_transaction.provider_name,
            )
            member = matched_member
            temporary_contributor = tc

        # Amount resolution
        if amount is not None:
            parsed_amount = Decimal(str(amount)).quantize(Decimal("0.01"))
        else:
            parsed_amount = payment_transaction.amount

        if parsed_amount <= Decimal("0.00"):
            raise ValidationError("Contribution amount must be greater than zero.")

        # Contribution Date
        parsed_date = None
        if contribution_date:
            if isinstance(contribution_date, datetime.date):
                parsed_date = contribution_date
            elif isinstance(contribution_date, str):
                parsed_date = datetime.date.fromisoformat(contribution_date.strip())
        elif payment_transaction.confirmed_at:
            parsed_date = payment_transaction.confirmed_at.date()
        else:
            parsed_date = timezone.localdate()

        clean_description = custom_type_description.strip() if custom_type_description else None
        if contribution_type.name.strip().lower() == "other" and not clean_description:
            raise ValidationError("Description is required when contribution type is 'Other'.")

        status = ContributionStatus.FLAGGED if flagged_for_review else ContributionStatus.CONFIRMED

        with transaction.atomic():
            contribution = Contribution.objects.create(
                member=member,
                temporary_contributor=temporary_contributor,
                payment_transaction=payment_transaction,
                contribution_type=contribution_type,
                custom_type_description=clean_description,
                amount=parsed_amount,
                currency=currency or payment_transaction.currency,
                payment_mode=PaymentMode.MOBILE_MONEY,
                entry_method=EntryMethod.AUTOMATIC,
                reference_number=payment_transaction.provider_reference,
                contribution_date=parsed_date,
                status=status,
                recorded_by=actor,
            )

            AuditService.log(
                action="AUTOMATIC_CONTRIBUTION_RECORDED",
                entity_type="Contribution",
                entity_id=contribution.id,
                user=actor,
                new_values={
                    "contribution_number": contribution.contribution_number,
                    "provider_reference": payment_transaction.provider_reference,
                    "amount": str(parsed_amount),
                    "contribution_type": contribution_type.name,
                    "status": status,
                    "member_id": str(member.id) if member else None,
                    "temporary_contributor_id": str(temporary_contributor.id) if temporary_contributor else None,
                },
                result=AuditResult.SUCCESS,
            )
            return contribution

    @classmethod
    def search_contributions(
        cls,
        query: str = "",
        contribution_type_id: str | None = None,
        payment_mode: str | None = None,
        entry_method: str | None = None,
        status: str | None = None,
        date_from: str | datetime.date | None = None,
        date_to: str | datetime.date | None = None,
        order_by: str = "-contribution_date",
    ):
        """
        Performs multi-criteria searching and filtering across contribution records.
        Supports searching by:
        - Contribution ID (e.g. CON-00001245)
        - Member name / number
        - Temporary contributor phone / name
        - Reference number
        - Provider reference
        - Receipt number (gracefully handled when no receipt exists)
        """
        qs = (
            Contribution.objects.select_related(
                "member",
                "temporary_contributor",
                "contribution_type",
                "payment_transaction",
                "recorded_by",
            )
            .prefetch_related("receipt")
            .all()
        )

        if query:
            clean_q = query.strip()
            qs = qs.filter(
                Q(contribution_number__icontains=clean_q)
                | Q(member__first_name__icontains=clean_q)
                | Q(member__middle_name__icontains=clean_q)
                | Q(member__last_name__icontains=clean_q)
                | Q(member__member_number__icontains=clean_q)
                | Q(temporary_contributor__phone_number__icontains=clean_q)
                | Q(temporary_contributor__first_name__icontains=clean_q)
                | Q(temporary_contributor__last_name__icontains=clean_q)
                | Q(reference_number__icontains=clean_q)
                | Q(payment_transaction__provider_reference__icontains=clean_q)
                | Q(receipt__receipt_number__icontains=clean_q)
            ).distinct()

        if contribution_type_id and contribution_type_id != "all":
            qs = qs.filter(contribution_type_id=contribution_type_id)

        if payment_mode and payment_mode != "all":
            qs = qs.filter(payment_mode=payment_mode)

        if entry_method and entry_method != "all":
            qs = qs.filter(entry_method=entry_method)

        if status and status != "all":
            qs = qs.filter(status=status)

        if date_from:
            if isinstance(date_from, str) and date_from.strip():
                try:
                    df = datetime.date.fromisoformat(date_from.strip())
                    qs = qs.filter(contribution_date__gte=df)
                except ValueError:
                    pass
            elif isinstance(date_from, datetime.date):
                qs = qs.filter(contribution_date__gte=date_from)

        if date_to:
            if isinstance(date_to, str) and date_to.strip():
                try:
                    dt = datetime.date.fromisoformat(date_to.strip())
                    qs = qs.filter(contribution_date__lte=dt)
                except ValueError:
                    pass
            elif isinstance(date_to, datetime.date):
                qs = qs.filter(contribution_date__lte=date_to)

        allowed_ordering = [
            "-contribution_date",
            "contribution_date",
            "-created_at",
            "created_at",
            "-amount",
            "amount",
            "contribution_number",
            "-contribution_number",
        ]
        if order_by in allowed_ordering:
            qs = qs.order_by(order_by, "-created_at")
        else:
            qs = qs.order_by("-contribution_date", "-created_at")

        return qs
