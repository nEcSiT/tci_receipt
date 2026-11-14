import datetime
import logging
import re
import uuid
from decimal import Decimal, InvalidOperation
from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ValidationError, PermissionDenied
from django.db import transaction
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
from apps.contributions.providers.base import PaymentResult, PaymentStatus
from apps.contributions.providers.factory import get_payment_provider
from apps.core.utils.phone import extract_first_name_from_provider_name
from apps.members.models import Member, TemporaryContributor, MemberStatus
from apps.members.services import ContributorIdentificationService
from apps.notifications.models import (
    ManualAction,
    ManualActionPriority,
    ManualActionStatus,
    ManualActionType,
    Notification,
    NotificationStatus,
    NotificationType,
)
from apps.notifications.services import (
    NotificationRetryService,
    NotificationService,
    NotificationTemplateService,
)
from apps.receipts.models import Receipt
from apps.receipts.services import ReceiptRetryService, ReceiptService

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
        coverage_year: int | str | None = None,
        coverage_months: list[int | str] | None = None,
    ) -> Contribution:
        """
        Records a validated manual church contribution against an existing active Member.
        Enforces strict role permissions, amount validation, type description requirements,
        flexible welfare coverage recording, and append-only financial audit trails.
        """
        from apps.contributions.models import WelfareCoverage

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
        is_other = contribution_type.name.strip().lower() == "other"
        is_welfare = contribution_type.name.strip().lower() == "welfare"

        if is_other:
            if not clean_description:
                raise ValidationError("Description is required when contribution type is 'Other'.")
        else:
            # PRD requirement: Do not retain an irrelevant description when user changes to another category
            clean_description = None

        # Validate welfare coverage if provided
        parsed_coverage_year = None
        parsed_coverage_months = []
        if is_welfare and coverage_year:
            try:
                parsed_coverage_year = int(str(coverage_year).strip())
                if parsed_coverage_year < 2000 or parsed_coverage_year > 2100:
                    raise ValidationError("Welfare coverage year must be between 2000 and 2100.")
            except (ValueError, TypeError):
                raise ValidationError("Invalid welfare coverage year.")

            if coverage_months:
                for m in coverage_months:
                    try:
                        m_int = int(str(m).strip())
                        if 1 <= m_int <= 12 and m_int not in parsed_coverage_months:
                            parsed_coverage_months.append(m_int)
                    except (ValueError, TypeError):
                        pass

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

        # 8. Record Welfare Coverage periods
        if is_welfare and parsed_coverage_year and parsed_coverage_months:
            for m in sorted(parsed_coverage_months):
                WelfareCoverage.objects.create(
                    contribution=contribution,
                    year=parsed_coverage_year,
                    month=m,
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
                "welfare_coverage": contribution.welfare_coverage_display if is_welfare else None,
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


class AutomaticPaymentService:
    """
    Core orchestrator for Milestone 6 Automatic Payments & Notifications:
    1. Receives and processes payment provider confirmations (MTN MoMo, Telecel Cash, etc.)
    2. Enforces merchant number isolation (church merchant number -> manual review, no contributor receipt)
    3. Handles payment statuses: Successful, Failed, Cancelled, Delayed/Pending
    4. Detects exact duplicates (Provider Reference + Contribution Type)
    5. Flags conflicting duplicate references with different contribution types for manual review
    6. Identifies contributors strictly by phone number (Member with single/multiple phones OR TemporaryContributor)
    7. Automatically generates receipts with retry limits (3 attempts) and Manual Action escalation
    8. Generates personalized thank-you messages using contribution-specific templates
    9. Dispatches SMS notifications with retry limits (3 attempts) and Manual Action escalation
    10. Enforces notification failure independence (notification failure never invalidates financial event)
    """

    @classmethod
    def process_payment_confirmation(
        cls,
        provider_reference: str,
        transaction_phone: str,
        amount: Decimal | str | float,
        contribution_type_name: str,
        custom_type_description: str | None = None,
        status: str = PaymentStatus.SUCCESSFUL,
        provider: str = "MTN_MOMO",
        provider_name: str | None = None,
        failure_code: str | None = None,
        failure_reason: str | None = None,
        currency: str = "GHS",
        provider_data: dict | None = None,
        actor: User | None = None,
        simulate_receipt_failure_count: int = 0,
        simulate_sms_failure_count: int = 0,
    ) -> dict:
        """
        Processes an automatic payment confirmation from an external provider
        and executes the complete end-to-end automatic giving workflow.
        """
        # 1. Parse and validate amount
        try:
            parsed_amount = Decimal(str(amount)).quantize(Decimal("0.01"))
        except (InvalidOperation, TypeError, ValueError):
            raise ValidationError("Contribution amount must be a valid numeric monetary value.")

        if parsed_amount <= Decimal("0.00"):
            raise ValidationError("Contribution amount must be greater than zero.")

        # Clean strings
        clean_phone = (transaction_phone or "").strip()
        clean_ref = (provider_reference or "").strip()
        clean_desc = (custom_type_description or "").strip() if custom_type_description else None

        # 2. Section 6.12: Merchant Number Handling
        # If transaction originates from the church's merchant number rather than a contributor:
        merchant_numbers = getattr(settings, "CHURCH_MERCHANT_NUMBERS", ["HLC_MERCHANT", "0240000000", "0550000000"])
        if clean_phone in merchant_numbers:
            payment_tx, _ = PaymentTransaction.objects.update_or_create(
                provider_reference=clean_ref,
                defaults={
                    "provider": provider,
                    "transaction_phone": clean_phone,
                    "provider_name": provider_name or "Church Merchant",
                    "amount": parsed_amount,
                    "currency": currency,
                    "status": PaymentTransactionStatus.SUCCESSFUL if status == PaymentStatus.SUCCESSFUL else status,
                    "confirmed_at": timezone.now() if status == PaymentStatus.SUCCESSFUL else None,
                    "provider_data": provider_data or {},
                },
            )
            # Create Manual Action
            action = ManualAction.objects.create(
                action_type=ManualActionType.MERCHANT_TRANSACTION,
                related_record_type="PaymentTransaction",
                related_record_id=payment_tx.id,
                description=(
                    f"Automatic payment of {currency} {parsed_amount} received from church merchant number "
                    f"{clean_phone} (Ref: {clean_ref}). Placed into manual review workflow without contributor receipt."
                ),
                attempt_count=1,
                priority=ManualActionPriority.HIGH,
                status=ManualActionStatus.OPEN,
            )
            # Notify authorized system user
            Notification.objects.create(
                notification_type=NotificationType.MERCHANT_TRANSACTION,
                recipient_phone=clean_phone,
                related_record_type="PaymentTransaction",
                related_record_id=payment_tx.id,
                message=f"Merchant transaction {clean_ref} received from church merchant number. Manual review required.",
                status=NotificationStatus.PENDING,
            )
            AuditService.log(
                action="MERCHANT_TRANSACTION_HELD",
                entity_type="PaymentTransaction",
                entity_id=payment_tx.id,
                user=actor,
                new_values={
                    "provider_reference": clean_ref,
                    "merchant_phone": clean_phone,
                    "amount": str(parsed_amount),
                    "manual_action_id": str(action.id),
                },
                result=AuditResult.SUCCESS,
            )
            return {
                "status": "MERCHANT_TRANSACTION",
                "payment_transaction": payment_tx,
                "manual_action": action,
                "contribution": None,
                "receipt": None,
                "notification": None,
            }

        # 3. Sections 6.3, 6.8, 6.9, 6.10: Handle Payment Status
        payment_tx, _ = PaymentTransaction.objects.update_or_create(
            provider_reference=clean_ref,
            defaults={
                "provider": provider,
                "transaction_phone": clean_phone,
                "provider_name": provider_name,
                "amount": parsed_amount,
                "currency": currency,
                "status": status,
                "failure_code": failure_code,
                "failure_reason": failure_reason,
                "provider_data": provider_data or {},
            },
        )

        # 3a. Section 6.8: Failed Payment
        if status == PaymentStatus.FAILED:
            payment_tx.status = PaymentTransactionStatus.FAILED
            payment_tx.failure_code = failure_code or "FAILED"
            payment_tx.failure_reason = failure_reason or "Payment provider reported transaction failure."
            payment_tx.save(update_fields=["status", "failure_code", "failure_reason", "updated_at"])

            AuditService.log(
                action="PAYMENT_TRANSACTION_FAILED",
                entity_type="PaymentTransaction",
                entity_id=payment_tx.id,
                user=actor,
                new_values={
                    "provider_reference": clean_ref,
                    "phone": clean_phone,
                    "amount": str(parsed_amount),
                    "failure_code": payment_tx.failure_code,
                    "failure_reason": payment_tx.failure_reason,
                },
                result=AuditResult.FAILURE,
            )
            return {
                "status": "FAILED",
                "payment_transaction": payment_tx,
                "contribution": None,
                "receipt": None,
                "notification": None,
            }

        # 3b. Section 6.9: Cancelled Payment
        if status == PaymentStatus.CANCELLED:
            payment_tx.status = PaymentTransactionStatus.CANCELLED
            payment_tx.failure_code = failure_code or "USER_CANCELLED"
            payment_tx.failure_reason = failure_reason or "Transaction was cancelled by the user."
            payment_tx.save(update_fields=["status", "failure_code", "failure_reason", "updated_at"])

            AuditService.log(
                action="PAYMENT_TRANSACTION_CANCELLED",
                entity_type="PaymentTransaction",
                entity_id=payment_tx.id,
                user=actor,
                new_values={
                    "provider_reference": clean_ref,
                    "phone": clean_phone,
                    "amount": str(parsed_amount),
                },
                result=AuditResult.SUCCESS,
            )
            return {
                "status": "CANCELLED",
                "payment_transaction": payment_tx,
                "contribution": None,
                "receipt": None,
                "notification": None,
            }

        # 3c. Section 6.10: Delayed / Pending Payment
        if status == PaymentStatus.PENDING:
            payment_tx.status = PaymentTransactionStatus.PENDING
            payment_tx.save(update_fields=["status", "updated_at"])

            AuditService.log(
                action="PAYMENT_TRANSACTION_PENDING",
                entity_type="PaymentTransaction",
                entity_id=payment_tx.id,
                user=actor,
                new_values={
                    "provider_reference": clean_ref,
                    "phone": clean_phone,
                    "amount": str(parsed_amount),
                },
                result=AuditResult.SUCCESS,
            )
            return {
                "status": "PENDING",
                "payment_transaction": payment_tx,
                "contribution": None,
                "receipt": None,
                "notification": None,
            }

        # Status is SUCCESSFUL -> confirm payment
        payment_tx.status = PaymentTransactionStatus.SUCCESSFUL
        payment_tx.confirmed_at = timezone.now()
        payment_tx.save(update_fields=["status", "confirmed_at", "updated_at"])

        # 4. Section 6.2: Validate Contribution Type
        c_type = ContributionType.objects.filter(name__iexact=contribution_type_name.strip()).first()
        if not c_type:
            raise ValidationError(f"Invalid contribution type '{contribution_type_name}'.")

        if c_type.name.strip().lower() == "other" and not clean_desc:
            raise ValidationError("Description is required when contribution type is 'Other'.")

        # 5. Section 6.11: Duplicate Detection
        # Rule: Provider Reference + Contribution Type
        existing_tx_contributions = Contribution.objects.filter(
            payment_transaction__provider_reference=clean_ref
        ).select_related("contribution_type")

        exact_duplicate = existing_tx_contributions.filter(contribution_type=c_type).first()
        if exact_duplicate:
            payment_tx.status = PaymentTransactionStatus.DUPLICATE
            payment_tx.failure_reason = (
                f"Duplicate transaction: Provider reference '{clean_ref}' with contribution type "
                f"'{c_type.name}' already recorded."
            )
            payment_tx.save(update_fields=["status", "failure_reason", "updated_at"])

            AuditService.log(
                action="DUPLICATE_CONTRIBUTION_BLOCKED",
                entity_type="PaymentTransaction",
                entity_id=payment_tx.id,
                user=actor,
                new_values={
                    "provider_reference": clean_ref,
                    "contribution_type": c_type.name,
                    "existing_contribution_number": exact_duplicate.contribution_number,
                },
                result=AuditResult.FAILURE,
            )
            existing_receipt = getattr(exact_duplicate, "receipt", None)
            return {
                "status": "DUPLICATE",
                "payment_transaction": payment_tx,
                "contribution": exact_duplicate,
                "receipt": existing_receipt,
                "notification": None,
            }

        # If same provider reference is received with a DIFFERENT contribution type -> Flag for review
        if existing_tx_contributions.exists():
            existing_names = [c.contribution_type.name for c in existing_tx_contributions]
            action = ManualAction.objects.create(
                action_type=ManualActionType.DUPLICATE_TRANSACTION_REVIEW,
                related_record_type="PaymentTransaction",
                related_record_id=payment_tx.id,
                description=(
                    f"Duplicate provider reference conflict: Reference '{clean_ref}' was previously recorded "
                    f"under '{', '.join(existing_names)}' but is now received with type '{c_type.name}'. "
                    f"Flagged for manual review."
                ),
                attempt_count=1,
                priority=ManualActionPriority.HIGH,
                status=ManualActionStatus.OPEN,
            )
            AuditService.log(
                action="CONTRIBUTION_FLAGGED_FOR_REVIEW",
                entity_type="PaymentTransaction",
                entity_id=payment_tx.id,
                user=actor,
                new_values={
                    "provider_reference": clean_ref,
                    "existing_types": existing_names,
                    "new_type": c_type.name,
                    "manual_action_id": str(action.id),
                },
                result=AuditResult.PARTIAL,
            )
            return {
                "status": "FLAGGED_FOR_REVIEW",
                "payment_transaction": payment_tx,
                "manual_action": action,
                "contribution": None,
                "receipt": None,
                "notification": None,
            }

        # 6. Sections 6.4, 6.5, 6.6: Automatic Contributor Identification
        member, temporary_contributor = ContributorIdentificationService.identify_contributor(
            phone_number=clean_phone,
            provider=provider,
            provider_name=provider_name,
        )

        # 7. Section 6.7: Record Successful Contribution
        contribution = Contribution.objects.create(
            member=member,
            temporary_contributor=temporary_contributor,
            payment_transaction=payment_tx,
            contribution_type=c_type,
            custom_type_description=clean_desc,
            amount=parsed_amount,
            currency=currency,
            payment_mode=PaymentMode.MOBILE_MONEY,
            entry_method=EntryMethod.AUTOMATIC,
            reference_number=clean_ref,
            contribution_date=payment_tx.confirmed_at.date() if payment_tx.confirmed_at else timezone.localdate(),
            status=ContributionStatus.CONFIRMED,
            recorded_by=actor,
        )

        AuditService.log(
            action="AUTOMATIC_CONTRIBUTION_RECORDED",
            entity_type="Contribution",
            entity_id=contribution.id,
            user=actor,
            new_values={
                "contribution_number": contribution.contribution_number,
                "provider_reference": clean_ref,
                "amount": str(parsed_amount),
                "contribution_type": c_type.name,
                "member_id": str(member.id) if member else None,
                "temporary_contributor_id": str(temporary_contributor.id) if temporary_contributor else None,
            },
            result=AuditResult.SUCCESS,
        )

        # 8. Sections 6.13, 6.20: Automatic Receipt Generation with Retries
        receipt = ReceiptRetryService.generate_receipt_with_retry(
            contribution=contribution,
            max_attempts=3,
            actor=actor,
            simulate_failure_count=simulate_receipt_failure_count,
        )

        # 9. Sections 6.14, 6.15, 6.17, 6.18, 6.19, 6.21: Thank-You Notification Workflow
        # Contributor personalization rule:
        # If member matches, use member's first name.
        # If no member matches, use provider-supplied first name from THIS transaction.
        # If only a full name or no usable name is supplied, neutral greeting ("Beloved") is used.
        first_name = None
        if member:
            first_name = member.first_name
        else:
            tx_payer_name = payment_tx.provider_name or provider_name
            first_name = extract_first_name_from_provider_name(tx_payer_name)

        receipt_number = receipt.receipt_number if receipt else ""
        secure_link = ""
        if receipt:
            token = ReceiptService.generate_secure_access_token(receipt)
            secure_link = ReceiptService.build_public_access_url(token)

        thank_you_message, template = NotificationTemplateService.build_thank_you_message(
            first_name=first_name,
            amount=parsed_amount,
            currency=currency,
            contribution_type_name=c_type.name,
            receipt_number=receipt_number,
            date_str=contribution.contribution_date.strftime("%d %b %Y"),
            secure_link=secure_link,
            contribution_type=c_type,
        )

        if simulate_sms_failure_count > 0:
            from apps.notifications.providers.factory import get_sms_provider
            get_sms_provider().fail_next_n_times_for_phone(clean_phone, simulate_sms_failure_count)

        # Prevent duplicate notifications from repeated payment callbacks
        existing_notification = Notification.objects.filter(
            related_record_type="Contribution",
            related_record_id=contribution.id,
            notification_type=NotificationType.THANK_YOU,
        ).first()

        if existing_notification:
            notification = existing_notification
        else:
            recipient_display_name = (
                member.full_name if member
                else (payment_tx.provider_name or provider_name or "Unverified Contributor")
            )
            # Send automatic-payment SMS strictly to the transaction phone number
            sms_destination_phone = payment_tx.transaction_phone or clean_phone
            try:
                notification = NotificationService.create_and_send_notification(
                    recipient_phone=sms_destination_phone,
                    message=thank_you_message,
                    notification_type=NotificationType.THANK_YOU,
                    related_record_type="Contribution",
                    related_record_id=contribution.id,
                    recipient_name=recipient_display_name,
                    template=template,
                    channel="SMS",
                    actor=actor,
                )

                # Automatic retry if initial attempt failed
                if notification.status == NotificationStatus.FAILED:
                    NotificationRetryService.retry_notification(notification, max_attempts=3, actor=actor)
            except Exception as sms_err:
                logger.error(
                    "SMS dispatch exception for contribution %s (phone %s): %s",
                    contribution.contribution_number,
                    sms_destination_phone,
                    sms_err,
                )
                # Ensure notification is fetched or recorded so contribution stays successful
                notification = Notification.objects.filter(
                    related_record_type="Contribution",
                    related_record_id=contribution.id,
                    notification_type=NotificationType.THANK_YOU,
                ).first()

        return {
            "status": "SUCCESSFUL",
            "payment_transaction": payment_tx,
            "contribution": contribution,
            "receipt": receipt,
            "notification": notification,
        }


class UssdService:
    """
    USSD Givings Experience (Section 6.1 & 6.2):
    Contributor -> USSD -> Contribution Type -> Amount -> Payment -> Provider Confirmation -> Phone ID -> Contribution -> Receipt -> Thank-you -> SMS + Link.
    Initial prompt begins strictly with: 'Welcome to TCI HLC Givings'
    """

    MENU_TYPES = {
        "1": "Tithe",
        "2": "Thanksgiving",
        "3": "Higher Life Partners",
        "4": "Building Project",
        "5": "Other",
    }

    @classmethod
    def handle_request(
        cls,
        session_id: str,
        phone_number: str,
        text: str = "",
        simulate_success: bool = True,
    ) -> dict:
        """
        Processes standard USSD input sequence (e.g. '', '1', '1*50', '5*Camp*100*1').
        Returns dictionary:
        {
            "message": str,
            "is_terminal": bool,
            "payment_result": Optional[dict]
        }
        """
        clean_text = (text or "").strip()
        if not clean_text:
            # Step 0: Welcome screen
            welcome_msg = (
                "Welcome to TCI HLC Givings\n"
                "1. Tithe\n"
                "2. Thanksgiving\n"
                "3. Higher Life Partners\n"
                "4. Building Project\n"
                "5. Other"
            )
            return {"message": welcome_msg, "is_terminal": False, "payment_result": None}

        parts = [p.strip() for p in clean_text.split("*") if p.strip()]
        if not parts:
            return {"message": "Invalid input. Please dial again.", "is_terminal": True, "payment_result": None}

        choice = parts[0]
        if choice not in cls.MENU_TYPES:
            return {
                "message": (
                    "Invalid selection. Please choose:\n"
                    "1. Tithe\n"
                    "2. Thanksgiving\n"
                    "3. Higher Life Partners\n"
                    "4. Building Project\n"
                    "5. Other"
                ),
                "is_terminal": False,
                "payment_result": None,
            }

        c_type_name = cls.MENU_TYPES[choice]

        # Standard Types: 1-4 (Tithe, Thanksgiving, Partners, Building Project)
        if choice in ["1", "2", "3", "4"]:
            if len(parts) == 1:
                return {
                    "message": f"Enter amount for {c_type_name} (GHS):",
                    "is_terminal": False,
                    "payment_result": None,
                }
            elif len(parts) == 2:
                amount_str = parts[1]
                try:
                    amt = Decimal(amount_str)
                    if amt <= Decimal("0.00"):
                        raise ValueError()
                except Exception:
                    return {
                        "message": f"Invalid amount. Enter a positive amount for {c_type_name} (GHS):",
                        "is_terminal": False,
                        "payment_result": None,
                    }
                return {
                    "message": f"Authorize payment of GHS {amt:.2f} for {c_type_name}?\n1. Confirm\n2. Cancel",
                    "is_terminal": False,
                    "payment_result": None,
                }
            elif len(parts) >= 3:
                amount_str = parts[1]
                confirm_choice = parts[2]
                if confirm_choice == "2":
                    return {
                        "message": "Giving cancelled. God bless you.",
                        "is_terminal": True,
                        "payment_result": None,
                    }
                elif confirm_choice == "1":
                    amt = Decimal(amount_str)
                    ref = f"USSD-{uuid.uuid4().hex[:10].upper()}"
                    payment_result = None
                    if simulate_success:
                        payment_result = AutomaticPaymentService.process_payment_confirmation(
                            provider_reference=ref,
                            transaction_phone=phone_number,
                            amount=amt,
                            contribution_type_name=c_type_name,
                            status=PaymentStatus.SUCCESSFUL,
                        )
                    return {
                        "message": (
                            f"Payment prompt sent to {phone_number}. Please authorize on your phone to complete "
                            f"your {c_type_name} contribution. God bless you."
                        ),
                        "is_terminal": True,
                        "payment_result": payment_result,
                    }
                else:
                    return {
                        "message": "Invalid confirmation option.\n1. Confirm\n2. Cancel",
                        "is_terminal": False,
                        "payment_result": None,
                    }


        # Choice 5: Other
        else:
            if len(parts) == 1:
                return {
                    "message": "Enter description for your Other contribution:",
                    "is_terminal": False,
                    "payment_result": None,
                }
            elif len(parts) == 2:
                desc = parts[1]
                return {
                    "message": f"Enter amount for Other ({desc}) in GHS:",
                    "is_terminal": False,
                    "payment_result": None,
                }
            elif len(parts) == 3:
                desc = parts[1]
                amount_str = parts[2]
                try:
                    amt = Decimal(amount_str)
                    if amt <= Decimal("0.00"):
                        raise ValueError()
                except Exception:
                    return {
                        "message": f"Invalid amount. Enter a positive amount for Other ({desc}) in GHS:",
                        "is_terminal": False,
                        "payment_result": None,
                    }
                return {
                    "message": f"Authorize payment of GHS {amt:.2f} for Other ({desc})?\n1. Confirm\n2. Cancel",
                    "is_terminal": False,
                    "payment_result": None,
                }
            elif len(parts) >= 4:
                desc = parts[1]
                amount_str = parts[2]
                confirm_choice = parts[3]
                if confirm_choice == "2":
                    return {
                        "message": "Giving cancelled. God bless you.",
                        "is_terminal": True,
                        "payment_result": None,
                    }
                elif confirm_choice == "1":
                    amt = Decimal(amount_str)
                    ref = f"USSD-{uuid.uuid4().hex[:10].upper()}"
                    payment_result = None
                    if simulate_success:
                        payment_result = AutomaticPaymentService.process_payment_confirmation(
                            provider_reference=ref,
                            transaction_phone=phone_number,
                            amount=amt,
                            contribution_type_name="Other",
                            custom_type_description=desc,
                            status=PaymentStatus.SUCCESSFUL,
                        )
                    return {
                        "message": (
                            f"Payment prompt sent to {phone_number}. Please authorize on your phone to complete "
                            f"your Other ({desc}) contribution. God bless you."
                        ),
                        "is_terminal": True,
                        "payment_result": payment_result,
                    }
                else:
                    return {
                        "message": "Invalid confirmation option.\n1. Confirm\n2. Cancel",
                        "is_terminal": False,
                        "payment_result": None,
                    }


class BulkContributionService:
    """
    Shared business service managing bulk church contribution recording via
    both on-screen dynamic entry tables and Excel workbook uploads.
    Enforces shared validation, individual receipt generation, idempotency protection,
    and flexible Welfare coverage recording without artificial dues or balances.
    """

    @staticmethod
    def parse_welfare_months(val) -> list[int]:
        """Parses flexible month representations (names, numbers, ranges, or 'Whole Year')."""
        if not val:
            return []
        if isinstance(val, (list, tuple)):
            res = []
            for v in val:
                try:
                    i = int(v)
                    if 1 <= i <= 12 and i not in res:
                        res.append(i)
                except (ValueError, TypeError):
                    pass
            return sorted(res)

        s = str(val).strip().lower()
        if s in ["all", "whole year", "year", "1-12", "1 - 12", "all months"]:
            return list(range(1, 13))

        month_map = {
            "jan": 1, "january": 1,
            "feb": 2, "february": 2,
            "mar": 3, "march": 3,
            "apr": 4, "april": 4,
            "may": 5,
            "jun": 6, "june": 6,
            "jul": 7, "july": 7,
            "aug": 8, "august": 8,
            "sep": 9, "sept": 9, "september": 9,
            "oct": 10, "october": 10,
            "nov": 11, "november": 11,
            "dec": 12, "december": 12,
        }

        tokens = [t.strip() for t in re.split(r"[,;/]+", s) if t.strip()]
        res = []
        for t in tokens:
            if t in month_map:
                m = month_map[t]
                if m not in res:
                    res.append(m)
            else:
                try:
                    i = int(t)
                    if 1 <= i <= 12 and i not in res:
                        res.append(i)
                except ValueError:
                    pass
        return sorted(res)

    @classmethod
    def generate_template(cls) -> bytes:
        import io
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Contributions"

        headers = [
            "Member Identifier *",
            "Member Name Hint",
            "Category *",
            "Amount (GHS) *",
            "Payment Method *",
            "Payment Date *",
            "Reference Number",
            "Other Description",
            "Welfare Coverage Year",
            "Welfare Coverage Months",
            "New Member First Name",
            "New Member Last Name",
        ]
        ws.append(headers)

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
            ["0241234567", "Kwame Mensah", "Tithe", "500.00", "Mobile Money", timezone.localdate().isoformat(), "TX-109283", "", "", "", "", ""],
            ["MEM-00000001", "Ama Osei", "Offering", "100.00", "Cash", timezone.localdate().isoformat(), "", "", "", "", "", ""],
            ["0241234567", "Kwame Mensah", "Welfare", "200.00", "Bank Transaction", timezone.localdate().isoformat(), "BT-99120", "", str(timezone.localdate().year), "Whole Year", "", ""],
            ["MEM-00000002", "Kofi Mensah", "Other", "150.00", "Mobile Money", timezone.localdate().isoformat(), "TX-55110", "Youth Retreat Camp Support", "", "", "", ""],
        ]
        for r_idx, row_data in enumerate(examples, start=2):
            for c_idx, val in enumerate(row_data, start=1):
                cell = ws.cell(row=r_idx, column=c_idx)
                cell.value = str(val)
                cell.number_format = "@"
                cell.font = Font(name="Cabin", size=10)
                cell.border = thin_border

        col_widths = [22, 20, 16, 16, 18, 16, 20, 30, 22, 24, 22, 22]
        for col_idx, width in enumerate(col_widths, start=1):
            col_letter = openpyxl.utils.get_column_letter(col_idx)
            ws.column_dimensions[col_letter].width = width

        # Sheet 2: Reference & Instructions
        ws_info = wb.create_sheet(title="Instructions")
        ws_info.append(["TCI Higher Life Center — Bulk Contributions Upload Guidance"])
        ws_info.append([])
        ws_info.append(["Field Name", "Requirement", "Allowed Values / Format", "Notes"])

        field_notes = [
            ["Member Identifier", "Required", "Member Number (MEM-XXXX) or Phone (024XXXXXXX)", "Used to match existing active church member."],
            ["Category", "Required", "Tithe, Offering, Welfare, Thanksgiving, Higher Life Partners, Other", "Must match an active contribution category."],
            ["Amount (GHS)", "Required", "Positive number (e.g. 250.00)", "Must be greater than zero. No negative amounts."],
            ["Payment Method", "Required", "Cash, Mobile Money, Bank Transaction, Cheque, Other", "Payment channel."],
            ["Payment Date", "Required", "YYYY-MM-DD (e.g. 2026-11-12)", "When the money was received."],
            ["Other Description", "Required if Category is Other", "Plain text (e.g. Special Mission Fund)", "Mandatory purpose explanation when category is Other."],
            ["Welfare Coverage Year", "Required if Category is Welfare", "Year (e.g. 2026)", "Year covered by this payment."],
            ["Welfare Coverage Months", "Required if Category is Welfare", "'Whole Year' or 'Jan, Feb, Mar' or '1, 2, 3'", "Explicit months covered. Welfare has no fixed dues."],
            ["New Member First & Last Name", "Optional", "Plain text", "If member does not yet exist, provides details to register member."],
        ]
        for row in field_notes:
            ws_info.append(row)

        ws_info["A1"].font = Font(name="Cabin", size=14, bold=True, color="011345")
        for col_idx in range(1, 5):
            c = ws_info.cell(row=3, column=col_idx)
            c.fill = PatternFill(start_color="F16822", end_color="F16822", fill_type="solid")
            c.font = Font(name="Cabin", size=11, bold=True, color="FFFFFF")

        ws_info.column_dimensions["A"].width = 24
        ws_info.column_dimensions["B"].width = 18
        ws_info.column_dimensions["C"].width = 45
        ws_info.column_dimensions["D"].width = 50

        stream = io.BytesIO()
        wb.save(stream)
        return stream.getvalue()

    @classmethod
    def parse_and_validate_rows(cls, rows_data: list[dict], actor: User) -> dict:
        """
        Shared validation engine for both on-screen multi-row entries and Excel uploads.
        Ensures strict matching of members, category ordering, positive amounts,
        Other description requirement, and flexible Welfare coverage.
        """
        from apps.core.utils.phone import validate_phone_number, get_phone_lookup_variants
        from apps.contributions.models import ContributionType, PaymentMode
        from apps.members.models import Member, MemberPhoneNumber

        if not actor.is_system_administrator and not actor.has_permission("contribution.create"):
            raise PermissionDenied("You do not have permission to record contributions.")

        # Cache active categories
        active_types = {t.name.strip().lower(): t for t in ContributionType.objects.filter(is_active=True)}
        # Map id to type
        active_types_by_id = {str(t.id): t for t in active_types.values()}

        parsed_rows = []
        valid_rows = []
        error_rows = []
        total_amount = Decimal("0.00")
        currency_totals: dict[str, Decimal] = {}
        matched_members_set = set()
        proposed_members_count = 0
        welfare_periods_list = []

        for idx, r in enumerate(rows_data, start=1):
            row_errors = []
            status = "READY"

            # 1. Resolve Member
            member_obj = None
            proposed_new_member = None

            member_id = r.get("member_id", "").strip() if r.get("member_id") else ""
            member_ident = r.get("member_identifier", "").strip() if r.get("member_identifier") else ""

            if member_id:
                member_obj = Member.objects.filter(id=member_id, status=MemberStatus.ACTIVE).first()

            if not member_obj and member_ident:
                # Try by member_number
                member_obj = Member.objects.filter(member_number__iexact=member_ident, status=MemberStatus.ACTIVE).first()
                if not member_obj:
                    # Try by phone variants
                    variants = get_phone_lookup_variants(member_ident)
                    ph = MemberPhoneNumber.objects.filter(phone_number__in=variants, is_active=True).select_related("member").first()
                    if ph:
                        member_obj = ph.member

            # Check if proposing a new member in this row
            new_first = r.get("new_member_first_name", "").strip() if r.get("new_member_first_name") else ""
            new_last = r.get("new_member_last_name", "").strip() if r.get("new_member_last_name") else ""

            if not member_obj:
                if new_first and new_last:
                    # Validate new member registration details
                    new_phone = r.get("new_member_phone", member_ident).strip()
                    if new_phone:
                        clean_ph = clean_phone_number(new_phone)
                        if not validate_phone_number(clean_ph):
                            row_errors.append(f"Proposed new member phone '{new_phone}' is not a valid phone number.")
                            status = "ERROR"
                        else:
                            # Check conflict
                            variants = get_phone_lookup_variants(clean_ph)
                            conflict = MemberPhoneNumber.objects.filter(phone_number__in=variants, is_active=True).exists()
                            if conflict:
                                row_errors.append(f"Proposed new member phone '{new_phone}' is already registered to another member.")
                                status = "ERROR"
                            else:
                                proposed_new_member = {
                                    "first_name": new_first,
                                    "last_name": new_last,
                                    "phone_number": clean_ph,
                                }
                                proposed_members_count += 1
                    else:
                        proposed_new_member = {
                            "first_name": new_first,
                            "last_name": new_last,
                            "phone_number": "",
                        }
                        proposed_members_count += 1
                else:
                    ident_disp = member_ident or "(blank)"
                    row_errors.append(f"Member '{ident_disp}' not found in active church member database.")
                    status = "ERROR"
            else:
                matched_members_set.add(member_obj.id)

            # 2. Resolve Category
            category_raw = str(r.get("category", "")).strip()
            cat_id = str(r.get("category_id", "")).strip()
            c_type = None

            if cat_id and cat_id in active_types_by_id:
                c_type = active_types_by_id[cat_id]
            elif category_raw.lower() in active_types:
                c_type = active_types[category_raw.lower()]

            if not c_type:
                row_errors.append(f"Invalid or inactive contribution category '{category_raw}'.")
                status = "ERROR"

            # 3. Resolve Amount
            amount_raw = r.get("amount", "")
            amount_dec = None
            try:
                amount_dec = Decimal(str(amount_raw).strip()).quantize(Decimal("0.01"))
                if amount_dec <= Decimal("0.00"):
                    row_errors.append("Amount must be greater than zero.")
                    status = "ERROR"
            except (InvalidOperation, TypeError, ValueError):
                row_errors.append(f"Invalid monetary amount '{amount_raw}'.")
                status = "ERROR"

            currency = str(r.get("currency", "GHS")).strip().upper() or "GHS"

            # 4. Resolve Payment Mode
            mode_raw = str(r.get("payment_mode", r.get("payment_method", "CASH"))).strip()
            mode_normalized = None
            mode_map = {
                "cash": PaymentMode.CASH,
                "mobile money": PaymentMode.MOBILE_MONEY,
                "mobile_money": PaymentMode.MOBILE_MONEY,
                "momo": PaymentMode.MOBILE_MONEY,
                "bank transaction": PaymentMode.BANK_TRANSACTION,
                "bank_transaction": PaymentMode.BANK_TRANSACTION,
                "bank": PaymentMode.BANK_TRANSACTION,
                "cheque": PaymentMode.CHEQUE,
                "check": PaymentMode.CHEQUE,
                "other": PaymentMode.OTHER,
            }
            if mode_raw.lower() in mode_map:
                mode_normalized = mode_map[mode_raw.lower()]
            elif mode_raw in PaymentMode.values:
                mode_normalized = mode_raw
            else:
                row_errors.append(f"Invalid payment method '{mode_raw}'.")
                status = "ERROR"

            # 5. Resolve Date
            date_raw = r.get("contribution_date", r.get("payment_date", "") or "")
            parsed_date = None
            if date_raw:
                if isinstance(date_raw, datetime.date):
                    parsed_date = date_raw
                else:
                    try:
                        parsed_date = datetime.date.fromisoformat(str(date_raw).strip()[:10])
                    except ValueError:
                        row_errors.append(f"Invalid payment date format '{date_raw}'. Expected YYYY-MM-DD.")
                        status = "ERROR"
            if not parsed_date:
                parsed_date = timezone.localdate()

            # 6. Other Description Requirement
            other_desc = str(r.get("custom_type_description", r.get("other_description", "") or "")).strip()
            if c_type and c_type.name.strip().lower() == "other":
                if not other_desc:
                    row_errors.append("What is this contribution for? Description is required when category is 'Other'.")
                    status = "ERROR"
            else:
                other_desc = ""

            # 7. Welfare Coverage Requirements
            welfare_year = None
            welfare_months = []
            if c_type and c_type.name.strip().lower() == "welfare":
                yr_val = r.get("coverage_year", r.get("welfare_coverage_year", ""))
                if yr_val:
                    try:
                        welfare_year = int(str(yr_val).strip())
                        if welfare_year < 2000 or welfare_year > 2100:
                            row_errors.append("Welfare coverage year must be between 2000 and 2100.")
                            status = "ERROR"
                    except (ValueError, TypeError):
                        row_errors.append(f"Invalid coverage year '{yr_val}'.")
                        status = "ERROR"
                else:
                    welfare_year = parsed_date.year

                months_val = r.get("coverage_months", r.get("welfare_coverage_months", ""))
                welfare_months = cls.parse_welfare_months(months_val)
                if not welfare_months:
                    # Default to payment date month if omitted
                    welfare_months = [parsed_date.month]

                if welfare_year and welfare_months:
                    m_names = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
                    period_str = f"Whole Year {welfare_year}" if len(welfare_months) == 12 else f"{', '.join(m_names[m] for m in welfare_months)} {welfare_year}"
                    welfare_periods_list.append(period_str)

            row_summary = {
                "row_index": idx,
                "status": status,
                "errors": row_errors,
                "member_id": str(member_obj.id) if member_obj else "",
                "member_number": member_obj.member_number if member_obj else "",
                "member_name": member_obj.full_name if member_obj else (f"Proposed: {new_first} {new_last}" if proposed_new_member else ""),
                "proposed_new_member": proposed_new_member,
                "category_id": str(c_type.id) if c_type else "",
                "category_name": c_type.name if c_type else category_raw,
                "amount": str(amount_dec) if amount_dec is not None else str(amount_raw),
                "currency": currency,
                "payment_mode": mode_normalized or mode_raw,
                "payment_date": parsed_date.isoformat() if parsed_date else "",
                "reference_number": str(r.get("reference_number", "") or "").strip(),
                "custom_type_description": other_desc,
                "coverage_year": welfare_year,
                "coverage_months": welfare_months,
            }

            if status == "READY":
                valid_rows.append(row_summary)
                total_amount += amount_dec
                currency_totals[currency] = currency_totals.get(currency, Decimal("0.00")) + amount_dec
            else:
                error_rows.append(row_summary)

            parsed_rows.append(row_summary)

        return {
            "total_rows": len(parsed_rows),
            "valid_count": len(valid_rows),
            "error_count": len(error_rows),
            "has_errors": len(error_rows) > 0,
            "total_amount": total_amount,
            "currency_totals": currency_totals,
            "matched_members_count": len(matched_members_set),
            "proposed_members_count": proposed_members_count,
            "welfare_periods_summary": list(dict.fromkeys(welfare_periods_list)),
            "rows": parsed_rows,
            "valid_rows": valid_rows,
            "error_rows": error_rows,
        }

    @classmethod
    def parse_excel(cls, file_obj, actor: User) -> dict:
        """Parses an uploaded Excel contributions workbook and runs shared validation."""
        import openpyxl

        try:
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

        col_map = {}
        for idx, h in enumerate(raw_headers):
            if h is None:
                continue
            h_clean = str(h).strip().lower().replace("*", "").strip()
            if "member identifier" in h_clean or "member number" in h_clean or "identifier" in h_clean:
                col_map["member_identifier"] = idx
            elif "member name" in h_clean or "name hint" in h_clean:
                col_map["member_name_hint"] = idx
            elif "category" in h_clean or "type" in h_clean:
                col_map["category"] = idx
            elif "amount" in h_clean:
                col_map["amount"] = idx
            elif "currency" in h_clean:
                col_map["currency"] = idx
            elif "method" in h_clean or "mode" in h_clean:
                col_map["payment_mode"] = idx
            elif "date" in h_clean:
                col_map["contribution_date"] = idx
            elif "reference" in h_clean:
                col_map["reference_number"] = idx
            elif "other" in h_clean or "description" in h_clean:
                col_map["custom_type_description"] = idx
            elif "welfare coverage year" in h_clean or "coverage year" in h_clean:
                col_map["coverage_year"] = idx
            elif "welfare coverage month" in h_clean or "coverage month" in h_clean:
                col_map["coverage_months"] = idx
            elif "new member first" in h_clean:
                col_map["new_member_first_name"] = idx
            elif "new member last" in h_clean:
                col_map["new_member_last_name"] = idx

        extracted_rows = []
        for r_vals in rows_iter:
            if not r_vals or all(v is None or str(v).strip() == "" for v in r_vals):
                continue

            def get_c(key):
                idx = col_map.get(key)
                if idx is not None and idx < len(r_vals):
                    v = r_vals[idx]
                    if v is not None:
                        s = str(v).strip()
                        if s.startswith("="):
                            s = s[1:].strip()
                        return s
                return ""

            extracted_rows.append({
                "member_identifier": get_c("member_identifier"),
                "category": get_c("category"),
                "amount": get_c("amount"),
                "currency": get_c("currency") or "GHS",
                "payment_mode": get_c("payment_mode") or "Cash",
                "contribution_date": get_c("contribution_date"),
                "reference_number": get_c("reference_number"),
                "custom_type_description": get_c("custom_type_description"),
                "coverage_year": get_c("coverage_year"),
                "coverage_months": get_c("coverage_months"),
                "new_member_first_name": get_c("new_member_first_name"),
                "new_member_last_name": get_c("new_member_last_name"),
            })

        return cls.parse_and_validate_rows(extracted_rows, actor=actor)

    @classmethod
    @transaction.atomic
    def execute_batch(cls, actor: User, valid_rows: list[dict], idempotency_token: str) -> dict:
        """
        Executes a validated bulk contribution batch inside an atomic transaction.
        Creates individual contributions, generates individual official receipts,
        triggers category-specific thank-you SMS, and records audit logs.
        Idempotent: prevents duplicate submission from refreshes, retries, or double clicks.
        """
        from apps.receipts.services import ReceiptService
        from apps.members.services import MemberService
        from apps.contributions.models import Contribution, ContributionType, WelfareCoverage
        from apps.members.models import Member
        from apps.audit.models import AuditLog

        if not actor.is_system_administrator and not actor.has_permission("contribution.create"):
            raise PermissionDenied("You do not have permission to record contributions.")

        # Concurrency & Idempotency check
        existing_log = AuditLog.objects.filter(
            action="BULK_CONTRIBUTIONS_RECORDED",
            new_values__idempotency_token=idempotency_token,
        ).first()

        if existing_log and existing_log.new_values.get("contribution_ids"):
            existing_ids = existing_log.new_values["contribution_ids"]
            contributions = list(Contribution.objects.filter(id__in=existing_ids))
            receipts = [c.receipt for c in contributions if hasattr(c, "receipt")]
            total_amt = sum((c.amount for c in contributions), Decimal("0.00"))
            return {
                "contributions": contributions,
                "receipts": receipts,
                "count": len(contributions),
                "total_amount": total_amt,
                "is_replay": True,
            }

        created_contributions = []
        created_receipts = []
        total_amount = Decimal("0.00")

        # Map active types by id
        types_by_id = {str(t.id): t for t in ContributionType.objects.filter(is_active=True)}

        for r in valid_rows:
            member = None
            member_id = r.get("member_id")
            if member_id:
                member = Member.objects.filter(id=member_id).first()

            # Handle proposed new member creation
            proposed = r.get("proposed_new_member")
            if not member and proposed:
                if not (actor.is_system_administrator or actor.has_permission("member.create")):
                    raise PermissionDenied("You do not have permission to register new church members.")
                member = MemberService.create_member(
                    creator=actor,
                    first_name=proposed["first_name"],
                    last_name=proposed["last_name"],
                    phone_number=proposed.get("phone_number") or None,
                )

            if not member:
                continue

            c_type = types_by_id.get(r.get("category_id"))
            if not c_type:
                continue

            amount = Decimal(str(r["amount"])).quantize(Decimal("0.01"))
            currency = r.get("currency", "GHS")
            mode = r.get("payment_mode", "CASH")
            ref_num = r.get("reference_number") or None
            desc = r.get("custom_type_description") or None
            c_date = r.get("payment_date")

            parsed_date = timezone.localdate()
            if c_date:
                try:
                    parsed_date = datetime.date.fromisoformat(str(c_date)[:10])
                except ValueError:
                    pass

            is_other = c_type.name.strip().lower() == "other"
            is_welfare = c_type.name.strip().lower() == "welfare"

            clean_desc = desc if is_other else None

            # Create individual contribution
            contrib = Contribution.objects.create(
                member=member,
                temporary_contributor=None,
                payment_transaction=None,
                contribution_type=c_type,
                custom_type_description=clean_desc,
                amount=amount,
                currency=currency,
                payment_mode=mode,
                entry_method=EntryMethod.MANUAL,
                reference_number=ref_num,
                contribution_date=parsed_date,
                status=ContributionStatus.CONFIRMED,
                recorded_by=actor,
            )

            # Record Welfare Coverages
            if is_welfare and r.get("coverage_year") and r.get("coverage_months"):
                yr = int(r["coverage_year"])
                for m in r["coverage_months"]:
                    WelfareCoverage.objects.create(
                        contribution=contrib,
                        year=yr,
                        month=int(m),
                    )

            # Generate individual receipt (triggers personalized thank-you SMS upon commit)
            receipt = ReceiptService.generate_receipt(
                contrib,
                generated_by_user=actor,
                actor=actor,
            )

            created_contributions.append(contrib)
            created_receipts.append(receipt)
            total_amount += amount

        AuditService.log(
            action="BULK_CONTRIBUTIONS_RECORDED",
            entity_type="Contribution",
            entity_id=actor.id,
            user=actor,
            new_values={
                "idempotency_token": idempotency_token,
                "recorded_count": len(created_contributions),
                "total_amount": str(total_amount),
                "contribution_ids": [str(c.id) for c in created_contributions],
                "receipt_numbers": [rc.receipt_number for rc in created_receipts],
            },
            result=AuditResult.SUCCESS,
        )

        return {
            "contributions": created_contributions,
            "receipts": created_receipts,
            "count": len(created_contributions),
            "total_amount": total_amount,
            "is_replay": False,
        }
