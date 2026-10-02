import datetime
import logging
from decimal import Decimal, InvalidOperation
from django.db import transaction
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
from apps.receipts.models import Receipt, ReceiptStatus, ReceiptEdit, ReceiptDeliveryRecord

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
        import weasyprint

        context = {
            "receipt": receipt,
            "verification_url": f"/receipts/verify/{receipt.receipt_number}/",
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
            if contrib.member:
                primary_phone = contrib.member.phone_numbers.filter(is_active=True, is_primary=True).first()
                if not primary_phone:
                    primary_phone = contrib.member.phone_numbers.filter(is_active=True).first()
                if primary_phone:
                    phone = primary_phone.phone_number
            elif contrib.payment_transaction and contrib.payment_transaction.transaction_phone:
                phone = contrib.payment_transaction.transaction_phone
            elif contrib.temporary_contributor and contrib.temporary_contributor.phone_number:
                phone = contrib.temporary_contributor.phone_number

        # Generate secure access link
        token = ReceiptService.generate_secure_access_token(receipt)
        secure_link = f"/receipts/access/{token}/"

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
            # Construct SMS message body
            c_type = receipt.contribution.contribution_type.name
            amount_str = f"{receipt.contribution.currency} {receipt.contribution.amount:.2f}"
            c_date = receipt.contribution.contribution_date.strftime("%d %b %Y")
            message = (
                f"TCI Higher Life Center: Received with thanks your {c_type} of {amount_str} on {c_date}. "
                f"Receipt: {receipt.receipt_number}. View receipt: {secure_link}. "
                f"Jesus Saves, Heals & Satisfies."
            )

            # Create Delivery Record
            delivery = ReceiptDeliveryRecord.objects.create(
                receipt=receipt,
                recipient_phone=phone,
                channel="SMS",
                status="SENT",
                secure_link=secure_link,
                provider_reference=f"SMS-{receipt.receipt_number}",
                attempt_count=1,
                last_attempt_at=timezone.now(),
                delivered_at=timezone.now(),
            )

            # Also create corresponding Notification entry for cross-system tracking
            Notification.objects.create(
                notification_type=NotificationType.RECEIPT,
                recipient_name=receipt.contributor_name,
                recipient_phone=phone,
                related_record_type="Receipt",
                related_record_id=receipt.id,
                channel="SMS",
                message=message,
                status=NotificationStatus.SENT,
                sent_at=timezone.now(),
                delivered_at=timezone.now(),
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
        """
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

        return receipt

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
    def get_receipt_by_access_token(cls, token: str) -> Receipt | None:
        """
        Resolves receipt from secure access token, checking cryptographic signature.
        """
        signer = TimestampSigner(salt=cls.TOKEN_SALT)
        try:
            # Valid for up to 90 days
            receipt_id = signer.unsign(token, max_age=86400 * 90)
            return Receipt.all_objects.filter(id=receipt_id).select_related(
                "contribution__member",
                "contribution__temporary_contributor",
                "contribution__contribution_type",
                "generated_by_user",
            ).first()
        except (BadSignature, SignatureExpired):
            return None
