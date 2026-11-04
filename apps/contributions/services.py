import datetime
import logging
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
        first_name = None
        if member:
            first_name = member.first_name
        elif temporary_contributor:
            if temporary_contributor.first_name:
                first_name = temporary_contributor.first_name
            elif temporary_contributor.name:
                first_name = temporary_contributor.name.split()[0]

        receipt_number = receipt.receipt_number if receipt else ""
        secure_link = ""
        if receipt:
            token = ReceiptService.generate_secure_access_token(receipt)
            secure_link = f"/receipts/access/{token}/"

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

        notification = NotificationService.create_and_send_notification(
            recipient_phone=clean_phone,
            message=thank_you_message,
            notification_type=NotificationType.THANK_YOU,
            related_record_type="Contribution",
            related_record_id=contribution.id,
            recipient_name=contribution.member.full_name if contribution.member else (contribution.temporary_contributor.name if contribution.temporary_contributor else None),
            template=template,
            channel="SMS",
            actor=actor,
        )

        # Automatic retry if initial attempt failed
        if notification.status == NotificationStatus.FAILED:
            NotificationRetryService.retry_notification(notification, max_attempts=3, actor=actor)

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
