import datetime
import logging
from decimal import Decimal, InvalidOperation
from django.db import transaction, IntegrityError
from django.core.exceptions import ValidationError, PermissionDenied
from django.core.signing import TimestampSigner, BadSignature, SignatureExpired
from django.template.loader import render_to_string
from django.utils import timezone
from django.conf import settings

from apps.accounts.models import User
from apps.audit.models import AuditResult
from apps.audit.services import AuditService
from apps.contributions.models import Contribution, ContributionType, PaymentMode
from apps.core.services.id_generator import IdGenerator
from apps.core.services.storage import get_storage_service
from apps.notifications.models import (
    Notification,
    NotificationType,
    NotificationStatus,
    ManualAction,
    ManualActionType,
    ManualActionPriority,
    ManualActionStatus,
)
from apps.receipts.models import (
    Receipt,
    ReceiptStatus,
    ReceiptEdit,
    ReceiptDeliveryRecord,
    ReceiptGenerationAttempt,
)

logger = logging.getLogger(__name__)


class ReceiptPdfService:
    """
    Renders official TCI Higher Life Center receipt HTML and generates
    high-fidelity, production-grade PDF documents using WeasyPrint.
    Ensures that phone numbers are NEVER rendered in the document.
    """

    @classmethod
    def render_pdf(cls, receipt: Receipt) -> bytes:
        """
        Renders the PDF template for a receipt and compiles it into bytes.
        """
        import base64
        import weasyprint

        logo_path = settings.BASE_DIR / "static" / "img" / "tci_hlc-logo.png"
        logo_base64 = ""
        if logo_path.exists():
            try:
                with open(logo_path, "rb") as f:
                    logo_base64 = f"data:image/png;base64,{base64.b64encode(f.read()).decode('utf-8')}"
            except Exception:
                logo_base64 = ""

        context = {
            "receipt": receipt,
            "verification_url": f"/receipts/verify/{receipt.receipt_number}/",
            "logo_base64": logo_base64,
        }
        html_string = render_to_string("receipts/receipt_pdf.html", context)
        html = weasyprint.HTML(string=html_string)
        pdf_bytes = html.write_pdf()
        return pdf_bytes

    @classmethod
    def generate_and_store_pdf(cls, receipt: Receipt) -> str:
        """
        Compiles the PDF and persists it in the configured StorageService.
        Returns the storage key.
        """
        storage = get_storage_service()
        pdf_bytes = cls.render_pdf(receipt)
        date_folder = receipt.generated_at.strftime("%Y/%m")
        storage_key = f"receipts/{date_folder}/{receipt.receipt_number}.pdf"
        storage.save(storage_key, pdf_bytes)
        return storage_key


class ReceiptDeliveryService:
    """
    Handles automatic receipt delivery via SMS with secure PDF access links.
    Maintains delivery records, supports retry states, and guarantees that
    delivery failures never invalidate successful contributions or receipts.
    """

    @classmethod
    def dispatch_automatic_receipt(cls, receipt: Receipt, recipient_phone: str | None = None) -> ReceiptDeliveryRecord:
        """
        Dispatches receipt SMS delivery for automatic contributions.
        Creates a ReceiptDeliveryRecord and an outbound Notification.
        """
        # Resolve recipient phone
        phone = recipient_phone
        if not phone:
            contrib = receipt.contribution
            # Priority: for automatic payments, always use the actual transaction phone
            if contrib.payment_transaction and contrib.payment_transaction.transaction_phone:
                phone = contrib.payment_transaction.transaction_phone
            elif contrib.member:
                primary_phone = contrib.member.phone_numbers.filter(is_active=True, is_primary=True).first()
                if not primary_phone:
                    primary_phone = contrib.member.phone_numbers.filter(is_active=True).first()
                if primary_phone:
                    phone = primary_phone.phone_number
            elif contrib.temporary_contributor and contrib.temporary_contributor.phone_number:
                phone = contrib.temporary_contributor.phone_number

        # Generate secure access link
        token = ReceiptService.generate_secure_access_token(receipt)
        secure_link = ReceiptService.build_public_access_url(token)

        if not phone:
            # Record failed delivery due to missing phone; does NOT fail the contribution
            delivery = ReceiptDeliveryRecord.objects.create(
                receipt=receipt,
                recipient_phone="UNKNOWN",
                channel="SMS",
                status="FAILED",
                secure_link=secure_link,
                attempt_count=1,
                last_attempt_at=timezone.now(),
                failure_reason="No valid recipient phone number associated with contributor record.",
            )
            return delivery

        try:
            from apps.notifications.services import NotificationService, NotificationTemplateService
            from apps.core.utils.phone import extract_first_name_from_provider_name

            contribution = receipt.contribution
            provider_name = (contribution.payment_transaction.provider_name
                             if contribution.payment_transaction else None)
            first_name = (contribution.member.first_name if contribution.member
                          else extract_first_name_from_provider_name(provider_name))
            message, template = NotificationTemplateService.build_receipt_message(
                first_name=first_name,
                amount=contribution.amount,
                currency=contribution.currency,
                contribution_type_name=contribution.contribution_type.name,
                receipt_number=receipt.receipt_number,
                date_str=contribution.contribution_date.strftime("%d %b %Y"),
                secure_link=secure_link,
            )
            notification = NotificationService.create_and_send_notification(
                notification_type=NotificationType.RECEIPT,
                recipient_name=receipt.contributor_name,
                recipient_phone=phone,
                related_record_type="Receipt",
                related_record_id=receipt.id,
                message=message,
                template=template,
            )
            delivery = ReceiptDeliveryRecord.objects.create(
                receipt=receipt,
                recipient_phone=phone,
                channel="SMS",
                status=notification.status,
                secure_link=secure_link,
                provider_reference=notification.provider_reference,
                attempt_count=notification.attempt_count,
                last_attempt_at=timezone.now(),
                delivered_at=notification.delivered_at,
                failure_reason=notification.failure_reason,
            )

            logger.info("Automatic receipt %s dispatched to %s", receipt.receipt_number, phone)
            return delivery

        except Exception as e:
            logger.error("Failed to dispatch receipt delivery for %s: %s", receipt.receipt_number, e)
            delivery = ReceiptDeliveryRecord.objects.create(
                receipt=receipt,
                recipient_phone=phone,
                channel="SMS",
                status="FAILED",
                secure_link=secure_link,
                attempt_count=1,
                last_attempt_at=timezone.now(),
                failure_reason=str(e),
            )
            return delivery


class ReceiptService:
    """
    Core domain service managing Receipt lifecycle:
    - Unique collision-safe HLC-XXXXXXX number generation
    - PDF generation and storage
    - Automatic receipt immutability enforcement
    - Creator-only manual editing with field-level audit logging
    - Soft deletion with historical and administrator visibility
    - Privacy-preserving receipt verification
    - Secure PDF access token generation and resolution
    """

    TOKEN_SALT = "tci-hlc-receipt-secure-access"

    @classmethod
    @transaction.atomic
    def generate_receipt(
        cls,
        contribution: Contribution,
        generated_by_user: User | None = None,
        is_system: bool = False,
        actor: User | None = None,
    ) -> Receipt:
        """
        Generates an official Receipt for a given Contribution.
        If a receipt already exists, returns the existing record.
        Creates PDF, securely stores it, and records audit trail.
        For automatic contributions, dispatches SMS delivery.
        For manual contributions, dispatches personalized thank-you SMS upon generation.
        """
        # Concurrency protection: lock contribution row
        locked_contribution = Contribution.objects.select_for_update().filter(id=contribution.id).first()
        if locked_contribution:
            contribution = locked_contribution

        # If receipt already exists, return it
        existing = Receipt.all_objects.filter(contribution=contribution).first()
        if existing:
            return existing

        is_auto = is_system or (contribution.entry_method == "AUTOMATIC")

        if is_auto:
            gen_system = True
            gen_user = None
        else:
            gen_system = False
            gen_user = generated_by_user or contribution.recorded_by

        # Unique 7-digit receipt number
        receipt_number = IdGenerator.generate_receipt_number()
        date_folder = timezone.now().strftime("%Y/%m")
        storage_key = f"receipts/{date_folder}/{receipt_number}.pdf"

        receipt = Receipt.objects.create(
            contribution=contribution,
            receipt_number=receipt_number,
            generated_by_user=gen_user,
            generated_by_system=gen_system,
            pdf_storage_key=storage_key,
            status=ReceiptStatus.ACTIVE,
            generated_at=timezone.now(),
        )

        # Generate and store PDF
        try:
            stored_key = ReceiptPdfService.generate_and_store_pdf(receipt)
            if stored_key != storage_key:
                receipt.pdf_storage_key = stored_key
                receipt.save(update_fields=["pdf_storage_key"])
        except Exception as e:
            logger.error("Failed to generate PDF for receipt %s: %s", receipt.receipt_number, e)
            # Store fallback indicator; manual action can retry PDF generation
            receipt.pdf_storage_key = storage_key
            receipt.save(update_fields=["pdf_storage_key"])

        # Audit Log
        AuditService.log(
            action="RECEIPT_GENERATED",
            entity_type="Receipt",
            entity_id=receipt.id,
            user=actor or gen_user,
            new_values={
                "receipt_number": receipt.receipt_number,
                "contribution_number": contribution.contribution_number,
                "amount": str(contribution.amount),
                "currency": contribution.currency,
                "contribution_type": contribution.contribution_type.name,
                "payment_mode": contribution.payment_mode,
                "generated_by_system": gen_system,
                "generated_by_user": gen_user.email if gen_user else None,
                "pdf_storage_key": receipt.pdf_storage_key,
            },
            result=AuditResult.SUCCESS,
        )

        # If automatic receipt, trigger automatic receipt delivery
        if is_auto:
            ReceiptDeliveryService.dispatch_automatic_receipt(receipt)
        else:
            cls.dispatch_manual_thank_you_sms(receipt, actor=actor or gen_user)

        return receipt

    @classmethod
    def dispatch_manual_thank_you_sms(
        cls,
        receipt: Receipt,
        actor: User | None = None,
    ) -> Notification | None:
        """
        Triggers the personalized thank-you SMS for manual contributions upon receipt generation.
        Workflow:
        1. Resolve contributor name and active phone:
           - Use selected member's first name.
           - Active primary phone, falling back deterministically to another active phone.
           - Normalize and validate phone.
        2. Render personalized message using active category template (Tithe, Offering, Welfare,
           or generic fallback) with all supported variables.
        3. Prevent duplicates: database-backed protection via select_for_update and unique constraint.
        4. Persist one pending THANK_YOU notification in DB before dispatch.
        5. Dispatch via SMS provider after database transaction commits (on_commit).
        6. Failures (missing phone, gateway timeout/error) do not undo contribution or receipt.
        7. Retries retain original wording up to 3 attempts.
        """
        contribution = receipt.contribution
        member = contribution.member

        # Check if THANK_YOU notification already exists for this contribution
        existing = Notification.objects.filter(
            related_record_type="Contribution",
            related_record_id=contribution.id,
            notification_type=NotificationType.THANK_YOU,
        ).first()
        if existing:
            return existing

        from apps.core.utils.phone import clean_phone_number, validate_phone_number
        from apps.notifications.models import NotificationAttempt
        from apps.notifications.services import NotificationService, NotificationTemplateService

        # 1. First name resolution
        first_name = member.first_name if member else None
        if not first_name and contribution.temporary_contributor:
            first_name = contribution.temporary_contributor.first_name

        # 2. Recipient phone resolution: active primary phone, falling back deterministically
        raw_phone = None
        if member:
            primary_phone = member.phone_numbers.filter(is_active=True, is_primary=True).first()
            if not primary_phone:
                primary_phone = member.phone_numbers.filter(is_active=True).order_by("created_at", "id").first()
            if primary_phone:
                raw_phone = primary_phone.phone_number
        elif contribution.temporary_contributor and contribution.temporary_contributor.phone_number:
            raw_phone = contribution.temporary_contributor.phone_number

        # 3. Secure access link and template message rendering
        token = cls.generate_secure_access_token(receipt)
        secure_link = cls.build_public_access_url(token)

        message, template = NotificationTemplateService.build_thank_you_message(
            first_name=first_name,
            amount=contribution.amount,
            currency=contribution.currency,
            contribution_type_name=contribution.contribution_type.name,
            receipt_number=receipt.receipt_number,
            date_str=contribution.contribution_date.strftime("%d %b %Y"),
            secure_link=secure_link,
            contribution_type=contribution.contribution_type,
        )

        recipient_display_name = (
            member.full_name if member
            else (receipt.contributor_name or "Contributor")
        )

        # 4. Normalize and validate phone
        normalized_phone = clean_phone_number(raw_phone) if raw_phone else ""
        is_valid_phone = validate_phone_number(normalized_phone)

        if not raw_phone or not is_valid_phone:
            # Missing or invalid phone: Record delivery failure without undoing contribution or receipt
            fail_reason = (
                "No valid recipient phone number associated with contributor record."
                if not raw_phone
                else f"Invalid recipient phone number format: '{raw_phone}'."
            )
            try:
                with transaction.atomic():
                    notification = Notification.objects.create(
                        notification_type=NotificationType.THANK_YOU,
                        recipient_name=recipient_display_name,
                        recipient_phone=normalized_phone or "UNKNOWN",
                        related_record_type="Contribution",
                        related_record_id=contribution.id,
                        channel="SMS",
                        template=template,
                        message=message,
                        status=NotificationStatus.FAILED,
                        attempt_count=1,
                        failure_reason=fail_reason,
                    )
                    NotificationAttempt.objects.create(
                        notification=notification,
                        attempt_number=1,
                        status="FAILED",
                        failure_code="ERR_INVALID_PHONE",
                        failure_reason=fail_reason,
                    )
                    AuditService.log(
                        action="NOTIFICATION_FAILED",
                        entity_type="Notification",
                        entity_id=notification.id,
                        user=actor,
                        new_values={
                            "notification_type": notification.notification_type,
                            "recipient_phone": notification.recipient_phone,
                            "status": NotificationStatus.FAILED,
                            "failure_code": "ERR_INVALID_PHONE",
                            "failure_reason": fail_reason,
                        },
                        result=AuditResult.FAILURE,
                    )
                    logger.warning(
                        "Manual thank-you SMS delivery failed for contribution %s: %s",
                        contribution.contribution_number,
                        fail_reason,
                    )
                    return notification
            except IntegrityError:
                return Notification.objects.filter(
                    related_record_type="Contribution",
                    related_record_id=contribution.id,
                    notification_type=NotificationType.THANK_YOU,
                ).first()

        # 5. Persist pending notification with database-backed duplicate protection
        try:
            with transaction.atomic():
                notification = Notification.objects.create(
                    notification_type=NotificationType.THANK_YOU,
                    recipient_name=recipient_display_name,
                    recipient_phone=normalized_phone,
                    related_record_type="Contribution",
                    related_record_id=contribution.id,
                    channel="SMS",
                    template=template,
                    message=message,
                    status=NotificationStatus.PENDING,
                    attempt_count=0,
                )
        except IntegrityError:
            # Concurrently created by duplicate request
            return Notification.objects.filter(
                related_record_type="Contribution",
                related_record_id=contribution.id,
                notification_type=NotificationType.THANK_YOU,
            ).first()

        # 6. Dispatch through existing SMS provider strictly after database transaction commits
        def _dispatch_after_commit():
            try:
                NotificationService.dispatch_notification(notification.id, actor=actor)
            except Exception as e:
                logger.error(
                    "Error during after-commit dispatch for notification %s: %s",
                    notification.id,
                    e,
                )

        transaction.on_commit(_dispatch_after_commit)
        return notification

    @classmethod
    @transaction.atomic
    def edit_manual_receipt(
        cls,
        receipt: Receipt,
        user: User,
        changed_fields: dict,
        reason: str,
    ) -> Receipt:
        """
        Modifies a manual receipt subject to creator ownership, permissions,
        and field-level audit tracking.
        Automatic receipts are strictly immutable.
        """
        if not user.is_active:
            raise PermissionDenied("User account is inactive.")

        if receipt.status == ReceiptStatus.DELETED:
            raise ValidationError("Cannot edit a deleted receipt.")

        # Automatic receipt immutability check
        if receipt.generated_by_system:
            raise ValidationError("Automatic receipts are finalized and immutable. Normal modifications are strictly prohibited.")

        # Permission check: must have receipt.edit_own
        if not user.is_system_administrator and not user.has_permission("receipt.edit_own"):
            raise PermissionDenied("You do not have permission to edit receipts.")

        # Ownership rule: only the creator may edit the receipt
        if not user.is_system_administrator and receipt.generated_by_user != user:
            raise PermissionDenied("Only the creator of a manual receipt may edit it.")

        # Reason is required
        clean_reason = (reason or "").strip()
        if not clean_reason:
            raise ValidationError("A specific reason is required for every receipt modification.")

        contribution = receipt.contribution
        edits_made = []

        # Editable fields map
        allowed_fields = [
            "amount",
            "contribution_type_id",
            "payment_mode",
            "reference_number",
            "contribution_date",
            "custom_type_description",
        ]

        for field_name, new_val in changed_fields.items():
            if field_name not in allowed_fields:
                continue

            if field_name == "amount":
                try:
                    parsed_amount = Decimal(str(new_val)).quantize(Decimal("0.01"))
                except (InvalidOperation, TypeError, ValueError):
                    raise ValidationError("Amount must be a valid numeric monetary value.")
                if parsed_amount <= Decimal("0.00"):
                    raise ValidationError("Contribution amount must be greater than zero.")
                if parsed_amount != contribution.amount:
                    edits_made.append(("amount", str(contribution.amount), str(parsed_amount)))
                    contribution.amount = parsed_amount

            elif field_name == "contribution_type_id":
                c_type = ContributionType.objects.filter(id=new_val, is_active=True).first()
                if not c_type:
                    raise ValidationError("Invalid or inactive contribution type.")
                if c_type.id != contribution.contribution_type_id:
                    edits_made.append(("contribution_type", contribution.contribution_type.name, c_type.name))
                    contribution.contribution_type = c_type

            elif field_name == "payment_mode":
                if new_val not in PaymentMode.values:
                    raise ValidationError(f"Invalid payment mode '{new_val}'.")
                if new_val != contribution.payment_mode:
                    edits_made.append(("payment_mode", contribution.payment_mode, str(new_val)))
                    contribution.payment_mode = new_val

            elif field_name == "reference_number":
                clean_ref = (new_val or "").strip() or None
                if clean_ref != contribution.reference_number:
                    edits_made.append(("reference_number", str(contribution.reference_number or ""), str(clean_ref or "")))
                    contribution.reference_number = clean_ref

            elif field_name == "custom_type_description":
                clean_desc = (new_val or "").strip() or None
                if clean_desc != contribution.custom_type_description:
                    edits_made.append(("custom_type_description", str(contribution.custom_type_description or ""), str(clean_desc or "")))
                    contribution.custom_type_description = clean_desc

            elif field_name == "contribution_date":
                if isinstance(new_val, str):
                    try:
                        parsed_date = datetime.date.fromisoformat(new_val.strip())
                    except ValueError:
                        raise ValidationError("Invalid contribution date format. Use YYYY-MM-DD.")
                elif isinstance(new_val, datetime.date):
                    parsed_date = new_val
                else:
                    parsed_date = None

                if parsed_date and parsed_date != contribution.contribution_date:
                    edits_made.append(("contribution_date", str(contribution.contribution_date), str(parsed_date)))
                    contribution.contribution_date = parsed_date

        if not edits_made:
            return receipt

        # Validate contribution rules (e.g. Other requires description)
        contribution.full_clean()
        contribution.save()

        # Record ReceiptEdit audit records
        for field, old_v, new_v in edits_made:
            ReceiptEdit.objects.create(
                receipt=receipt,
                user=user,
                field_name=field,
                old_value=old_v,
                new_value=new_v,
                reason=clean_reason,
            )

        # Regenerate and update stored PDF
        ReceiptPdfService.generate_and_store_pdf(receipt)

        # Audit trail
        AuditService.log(
            action="RECEIPT_EDITED",
            entity_type="Receipt",
            entity_id=receipt.id,
            user=user,
            old_values={f: old for f, old, _ in edits_made},
            new_values={f: new for f, _, new in edits_made},
            result=AuditResult.SUCCESS,
        )

        logger.info("Receipt %s edited by %s. Fields modified: %s", receipt.receipt_number, user.email, [f for f, _, _ in edits_made])
        return receipt

    @classmethod
    @transaction.atomic
    def delete_manual_receipt(
        cls,
        receipt: Receipt,
        user: User,
        reason: str = "",
    ) -> Receipt:
        """
        Soft-deletes a manual receipt according to permission rules.
        Automatic receipts are immutable and cannot be deleted.
        """
        if not user.is_active:
            raise PermissionDenied("User account is inactive.")

        if receipt.generated_by_system:
            raise ValidationError("Automatic receipts are immutable and cannot be deleted.")

        # Permission check
        if not user.is_system_administrator and not user.has_permission("receipt.delete_own"):
            raise PermissionDenied("You do not have permission to delete receipts.")

        # Ownership rule
        if not user.is_system_administrator and receipt.generated_by_user != user:
            raise PermissionDenied("Only the creator of a manual receipt may delete it.")

        # Soft delete
        receipt.soft_delete(user=user)

        # Audit trail
        AuditService.log(
            action="RECEIPT_DELETED",
            entity_type="Receipt",
            entity_id=receipt.id,
            user=user,
            new_values={
                "receipt_number": receipt.receipt_number,
                "status": ReceiptStatus.DELETED,
                "deleted_by": user.email,
                "reason": (reason or "").strip(),
            },
            result=AuditResult.SUCCESS,
        )

        logger.info("Manual receipt %s soft-deleted by %s", receipt.receipt_number, user.email)
        return receipt

    @classmethod
    def verify_receipt(cls, receipt_number: str) -> dict:
        """
        Public/administrative verification of a receipt using its unique number.
        Validates format, checks existence, confirms active/deleted status,
        and strictly excludes contributor phone numbers.
        """
        clean_num = (receipt_number or "").strip().upper()

        if not clean_num:
            return {
                "is_valid": False,
                "status": "NOT_FOUND",
                "message": "Please enter a valid receipt number (e.g., HLC-5831047).",
            }

        receipt = (
            Receipt.all_objects.filter(receipt_number=clean_num)
            .select_related(
                "contribution__member",
                "contribution__temporary_contributor",
                "contribution__contribution_type",
                "generated_by_user",
            )
            .first()
        )

        if not receipt:
            return {
                "is_valid": False,
                "status": "NOT_FOUND",
                "receipt_number": clean_num,
                "message": "No receipt matching this number was found in the official church registry.",
            }

        if receipt.status == ReceiptStatus.DELETED:
            return {
                "is_valid": False,
                "status": "DELETED",
                "receipt_number": receipt.receipt_number,
                "contributor_name": receipt.contributor_name,
                "contribution_date": receipt.contribution.contribution_date,
                "amount": receipt.contribution.amount,
                "currency": receipt.contribution.currency,
                "contribution_type": receipt.contribution.contribution_type.name,
                "message": "This receipt has been revoked / deleted and is no longer valid.",
            }

        return {
            "is_valid": True,
            "status": "VALID",
            "receipt_number": receipt.receipt_number,
            "contributor_name": receipt.contributor_name,
            "amount": receipt.contribution.amount,
            "currency": receipt.contribution.currency,
            "contribution_type": receipt.contribution.contribution_type.name,
            "payment_mode": receipt.contribution.get_payment_mode_display(),
            "contribution_date": receipt.contribution.contribution_date,
            "generated_at": receipt.generated_at,
            "generated_by": receipt.generated_by_display,
            "reference_number": receipt.contribution.reference_number,
            "message": "This is a valid official receipt issued by TCI Higher Life Center.",
        }

    @classmethod
    def generate_secure_access_token(cls, receipt: Receipt) -> str:
        """
        Generates a tamper-proof timestamped signature token for public SMS download.
        """
        signer = TimestampSigner(salt=cls.TOKEN_SALT)
        return signer.sign(str(receipt.id))

    @classmethod
    def build_public_access_url(cls, token: str) -> str:
        """
        Builds a complete, trusted public application address for SMS receipt access.
        Enforces HTTPS in production and prevents localhost addresses or relative paths
        from being dispatched to external contributors.
        """
        base = getattr(settings, "PUBLIC_APP_URL", "")
        if not base:
            base = "https://portal.tcihlc.org"
        base = base.strip().rstrip("/")
        if not base.startswith("http://") and not base.startswith("https://"):
            base = f"https://{base}"
        # In non-DEBUG or production, force https
        if not getattr(settings, "DEBUG", False) and base.startswith("http://"):
            base = "https://" + base[len("http://"):]
        return f"{base}/receipts/access/{token}/"

    @classmethod
    def resolve_access_token(cls, token: str) -> tuple[Receipt | None, str | None]:
        """
        Resolves receipt from secure access token, returning (receipt, error_code).
        error_code can be None, "EXPIRED", "INVALID", or "NOT_FOUND".
        """
        signer = TimestampSigner(salt=cls.TOKEN_SALT)
        max_age = getattr(settings, "RECEIPT_ACCESS_TOKEN_MAX_AGE", 86400)
        try:
            receipt_id = signer.unsign(token, max_age=max_age)
        except SignatureExpired:
            return None, "EXPIRED"
        except (BadSignature, Exception):
            return None, "INVALID"

        receipt = Receipt.all_objects.filter(id=receipt_id).select_related(
            "contribution__member",
            "contribution__temporary_contributor",
            "contribution__contribution_type",
            "generated_by_user",
        ).first()
        if not receipt:
            return None, "NOT_FOUND"
        return receipt, None

    @classmethod
    def get_receipt_by_access_token(cls, token: str) -> Receipt | None:
        """
        Resolves receipt from secure access token, checking cryptographic signature.
        """
        receipt, _ = cls.resolve_access_token(token)
        return receipt


class ReceiptRetryService:
    """
    Manages automatic receipt generation with up to 3 retry attempts (Section 6.20).
    Preserves attempt history, creates a Manual Action on the 3rd failed attempt,
    and guarantees the underlying contribution remains valid and intact.
    """

    MAX_ATTEMPTS = 3

    @classmethod
    def generate_receipt_with_retry(
        cls,
        contribution: Contribution,
        max_attempts: int = MAX_ATTEMPTS,
        actor: User | None = None,
        simulate_failure_count: int = 0,
    ) -> Receipt | None:
        """
        Attempts automatic receipt generation up to max_attempts.
        Records each attempt in ReceiptGenerationAttempt.
        If all attempts fail, creates a Manual Action.
        Never discards or modifies the contribution.
        """
        existing = Receipt.all_objects.filter(contribution=contribution).first()
        if existing:
            return existing

        current_attempts = ReceiptGenerationAttempt.objects.filter(contribution=contribution).count()
        remaining_simulate = simulate_failure_count

        for attempt_no in range(current_attempts + 1, max_attempts + 1):
            try:
                if remaining_simulate > 0:
                    remaining_simulate -= 1
                    raise RuntimeError("Simulated receipt PDF rendering / storage failure.")

                receipt = ReceiptService.generate_receipt(
                    contribution=contribution,
                    is_system=True,
                    actor=actor,
                )
                ReceiptGenerationAttempt.objects.create(
                    contribution=contribution,
                    attempt_number=attempt_no,
                    status="SUCCESS",
                )
                AuditService.log(
                    action="RECEIPT_GENERATION_SUCCESS",
                    entity_type="Receipt",
                    entity_id=receipt.id,
                    user=actor,
                    new_values={
                        "attempt_number": attempt_no,
                        "receipt_number": receipt.receipt_number,
                        "contribution_number": contribution.contribution_number,
                    },
                    result=AuditResult.SUCCESS,
                )
                return receipt

            except Exception as e:
                err_msg = str(e)
                ReceiptGenerationAttempt.objects.create(
                    contribution=contribution,
                    attempt_number=attempt_no,
                    status="FAILED",
                    failure_reason=err_msg,
                )
                logger.warning(
                    "Receipt generation attempt #%d failed for contribution %s: %s",
                    attempt_no,
                    contribution.contribution_number,
                    err_msg,
                )
                AuditService.log(
                    action="RECEIPT_GENERATION_FAILED_ATTEMPT",
                    entity_type="Contribution",
                    entity_id=contribution.id,
                    user=actor,
                    new_values={
                        "attempt_number": attempt_no,
                        "error": err_msg,
                        "contribution_number": contribution.contribution_number,
                    },
                    result=AuditResult.FAILURE,
                )

                if attempt_no >= max_attempts:
                    # Create Manual Action on 3rd failure
                    action = ManualAction.objects.filter(
                        related_record_type="Contribution",
                        related_record_id=contribution.id,
                        action_type=ManualActionType.RECEIPT_GENERATION_FAILED,
                    ).first()
                    if not action:
                        action = ManualAction.objects.create(
                            action_type=ManualActionType.RECEIPT_GENERATION_FAILED,
                            related_record_type="Contribution",
                            related_record_id=contribution.id,
                            description=(
                                f"Automatic receipt generation failed {max_attempts} times for contribution "
                                f"{contribution.contribution_number}. Failure: {err_msg}"
                            ),
                            attempt_count=max_attempts,
                            priority=ManualActionPriority.HIGH,
                            status=ManualActionStatus.OPEN,
                        )
                        AuditService.log(
                            action="RECEIPT_GENERATION_MANUAL_ACTION_REQUIRED",
                            entity_type="ManualAction",
                            entity_id=action.id,
                            user=actor,
                            new_values={
                                "contribution_id": str(contribution.id),
                                "contribution_number": contribution.contribution_number,
                                "attempt_count": max_attempts,
                                "failure_reason": err_msg,
                            },
                            result=AuditResult.PARTIAL,
                        )
                    return None

        return None
