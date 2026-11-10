import logging
import re
import string
import uuid
from decimal import Decimal
from typing import Any, Dict, List, Optional, Set, Tuple
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.audit.models import AuditResult
from apps.audit.services import AuditService
from apps.contributions.models import ContributionType
from apps.notifications.models import (
    ManualAction,
    ManualActionPriority,
    ManualActionStatus,
    ManualActionType,
    Notification,
    NotificationAttempt,
    NotificationStatus,
    NotificationTemplate,
    NotificationType,
)
from apps.notifications.providers.factory import get_sms_provider
from apps.notifications.providers.base import SmsResult

logger = logging.getLogger(__name__)


ALLOWED_PLACEHOLDERS: Set[str] = {
    "first_name",
    "amount",
    "currency",
    "contribution_type",
    "receipt_number",
    "date",
    "secure_link",
}


class NotificationTemplateService:
    """
    Manages configurable notification templates supporting approved variable substitutions:
    {first_name}, {amount}, {currency}, {contribution_type}, {receipt_number}, {date}, {secure_link}.
    Enforces validation of placeholders and rejects unsupported ones with clear errors.
    """

    ALLOWED_PLACEHOLDERS = ALLOWED_PLACEHOLDERS

    @classmethod
    def extract_placeholders(cls, template_body: str) -> Set[str]:
        """Extracts all variable placeholder names enclosed in braces."""
        if not template_body:
            return set()
        formatter = string.Formatter()
        placeholders = set()
        for _, field_name, _, _ in formatter.parse(template_body):
            if field_name is not None:
                placeholders.add(field_name)
        return placeholders

    @classmethod
    def validate_template_body(cls, template_body: str) -> Set[str]:
        """
        Validates template placeholders.
        Rejects unsupported placeholders, format specifiers, conversion flags,
        and attribute/item lookups with a clear, user-facing ValidationError.
        """
        if not template_body or not template_body.strip():
            raise ValidationError("Template wording cannot be blank.")

        formatter = string.Formatter()
        placeholders = set()
        try:
            parsed = list(formatter.parse(template_body))
        except ValueError as exc:
            raise ValidationError("Invalid placeholder syntax: check opening and closing braces.") from exc
        for _, field_name, format_spec, conversion in parsed:
            if field_name is not None:
                if format_spec or conversion:
                    raise ValidationError(
                        f"Format specifiers and conversion modifiers are not allowed in template placeholders (found {{{field_name}}})."
                    )
                if "." in field_name or "[" in field_name:
                    raise ValidationError(
                        f"Attribute and index lookups are not allowed in template placeholders (found {{{field_name}}})."
                    )
                placeholders.add(field_name)

        unsupported = sorted(placeholders - cls.ALLOWED_PLACEHOLDERS)
        if unsupported:
            unsupported_formatted = ", ".join([f"{{{p}}}" for p in unsupported])
            allowed_formatted = ", ".join([f"{{{p}}}" for p in sorted(cls.ALLOWED_PLACEHOLDERS)])
            raise ValidationError(
                f"Unsupported placeholder(s): {unsupported_formatted}. "
                f"Allowed placeholders are: {allowed_formatted}."
            )
        return placeholders

    @classmethod
    def render_template(cls, template_body: str, context: Dict[str, Any]) -> str:
        """
        Renders the template with the provided context dictionary.
        Validates that all placeholders in the template are allowed and that no
        unresolved placeholders remain in the output message.
        """
        cls.validate_template_body(template_body)
        placeholders = cls.extract_placeholders(template_body)

        mapped_ctx = {}
        for p in placeholders:
            val = context.get(p)
            mapped_ctx[p] = "" if val is None else str(val)

        # Also supply any additional context items (e.g. currency if provided)
        for k, v in context.items():
            if k not in mapped_ctx and v is not None:
                mapped_ctx[k] = str(v)

        rendered = template_body.format_map(mapped_ctx)

        # Check for unresolved placeholders (e.g. misspelled or empty braces)
        unresolved = re.findall(r"\{([a-zA-Z0-9_]+)\}", rendered)
        if unresolved:
            raise ValidationError(
                f"Rendered SMS contains unresolved placeholder(s): {', '.join([f'{{{p}}}' for p in unresolved])}"
            )
        return rendered

    @classmethod
    def get_active_template(
        cls,
        notification_type: str,
        contribution_type: Optional[ContributionType] = None,
    ) -> Optional[NotificationTemplate]:
        """
        Retrieves the active template for a given notification purpose and contribution category.
        1. Checks for a category-specific active template.
        2. Falls back to a generic active template (contribution_type is None).
        """
        if contribution_type:
            cat_template = NotificationTemplate.objects.filter(
                notification_type=notification_type,
                contribution_type=contribution_type,
                is_active=True,
            ).first()
            if cat_template:
                return cat_template

        # Generic fallback template
        return NotificationTemplate.objects.filter(
            notification_type=notification_type,
            contribution_type__isnull=True,
            is_active=True,
        ).first()

    @classmethod
    def get_thank_you_template(
        cls,
        contribution_type: Optional[ContributionType] = None,
    ) -> Optional[NotificationTemplate]:
        """Shortcut for getting the active Thank You template."""
        return cls.get_active_template(
            notification_type=NotificationType.THANK_YOU,
            contribution_type=contribution_type,
        )

    @classmethod
    def get_receipt_template(cls) -> Optional[NotificationTemplate]:
        """Shortcut for getting the active Receipt Delivery template."""
        return cls.get_active_template(
            notification_type=NotificationType.RECEIPT,
            contribution_type=None,
        )

    @classmethod
    def build_thank_you_message(
        cls,
        first_name: Optional[str],
        amount: Decimal,
        currency: str = "GHS",
        contribution_type_name: str = "Contribution",
        receipt_number: str = "",
        date_str: str = "",
        secure_link: str = "",
        contribution_type: Optional[ContributionType] = None,
    ) -> Tuple[str, Optional[NotificationTemplate]]:
        """
        Constructs the personalized thank-you SMS message body.
        Supports variables: {first_name}, {amount}, {currency}, {contribution_type},
        {receipt_number}, {date}, {secure_link}.
        Does NOT mandate or force-append receipt number/link unless the template includes them.
        Uses a neutral greeting ("Beloved") when no usable first name is supplied.
        """
        clean_first_name = (first_name or "").strip()
        display_name = clean_first_name if clean_first_name else "Beloved"

        context = {
            "first_name": display_name,
            "amount": f"{amount:.2f}",
            "currency": currency,
            "contribution_type": contribution_type_name,
            "receipt_number": receipt_number,
            "date": date_str,
            "secure_link": secure_link,
        }

        template = cls.get_thank_you_template(contribution_type)
        if template and template.template_body:
            message = cls.render_template(template.template_body, context)
        else:
            # Clearly configured generic fallback per PRD & specifications
            message = (
                f"Dear {display_name}, thank you for your generous contribution of {amount:.2f} {currency} "
                f"towards {contribution_type_name} to TCI Higher Life Center. God bless you abundantly!"
            )

        return message, template

    @classmethod
    def build_receipt_message(
        cls,
        first_name: Optional[str],
        amount: Decimal,
        currency: str = "GHS",
        contribution_type_name: str = "Contribution",
        receipt_number: str = "",
        date_str: str = "",
        secure_link: str = "",
    ) -> Tuple[str, Optional[NotificationTemplate]]:
        """Constructs the official receipt delivery message body."""
        clean_first_name = (first_name or "").strip()
        display_name = clean_first_name if clean_first_name else "Beloved"

        context = {
            "first_name": display_name,
            "amount": f"{amount:.2f}",
            "currency": currency,
            "contribution_type": contribution_type_name,
            "receipt_number": receipt_number,
            "date": date_str,
            "secure_link": secure_link,
        }

        template = cls.get_receipt_template()
        if template and template.template_body:
            message = cls.render_template(template.template_body, context)
        else:
            message = (
                f"Dear {display_name}, thank you for your contribution of {amount:.2f} {currency} "
                f"({contribution_type_name}) to TCI Higher Life Center on {date_str}. "
                f"Your official receipt is {receipt_number}. View/download receipt: {secure_link}"
            )

        return message, template

    @classmethod
    @transaction.atomic
    def save_and_activate_template(
        cls,
        template: NotificationTemplate,
        actor: Optional[User] = None,
    ) -> NotificationTemplate:
        """
        Validates, saves, and activates a template.
        Guarantees that no ambiguous duplicate active templates exist for the same
        (notification_type, contribution_type) pair by deactivating any existing one.
        """
        cls.validate_template_body(template.template_body)
        template.is_active = True
        template.updated_by = actor
        template.save()

        # Deactivate any duplicate active templates for same pair
        NotificationTemplate.objects.filter(
            notification_type=template.notification_type,
            contribution_type=template.contribution_type,
            is_active=True,
        ).exclude(id=template.id).update(is_active=False)

        AuditService.log(
            action="SMS_TEMPLATE_SAVED_AND_ACTIVATED",
            entity_type="NotificationTemplate",
            entity_id=template.id,
            user=actor,
            new_values={
                "name": template.name,
                "notification_type": template.notification_type,
                "contribution_type_id": str(template.contribution_type.id) if template.contribution_type else None,
                "is_active": True,
            },
            result=AuditResult.SUCCESS,
        )
        return template

    @classmethod
    @transaction.atomic
    def save_template(
        cls,
        name: str,
        notification_type: str,
        template_body: str,
        template_id: Optional[uuid.UUID] = None,
        contribution_type: Optional[ContributionType] = None,
        is_active: bool = True,
        actor: Optional[User] = None,
    ) -> NotificationTemplate:
        """
        Saves or updates a notification template with explicit permission verification,
        placeholder validation, duplicate de-duplication, and audit logging.
        """
        if actor and not (
            getattr(actor, "is_system_administrator", False)
            or actor.has_permission("template.manage")
            or actor.has_permission("notification.manage")
        ):
            raise ValidationError("You do not have permission to manage SMS templates.")

        cls.validate_template_body(template_body)

        if template_id:
            template = NotificationTemplate.objects.get(id=template_id)
            template.name = name
            template.notification_type = notification_type
            template.contribution_type = contribution_type
            template.template_body = template_body
            template.is_active = is_active
            template.updated_by = actor
        else:
            template = NotificationTemplate(
                name=name,
                notification_type=notification_type,
                contribution_type=contribution_type,
                template_body=template_body,
                is_active=is_active,
                created_by=actor,
                updated_by=actor,
            )

        if is_active:
            return cls.save_and_activate_template(template, actor=actor)
        else:
            template.save()
            AuditService.log(
                action="SMS_TEMPLATE_SAVED",
                entity_type="NotificationTemplate",
                entity_id=template.id,
                user=actor,
                new_values={"name": template.name, "is_active": False},
                result=AuditResult.SUCCESS,
            )
            return template

    @classmethod
    def render_preview(
        cls,
        template_body: str,
        sample_context: Optional[Dict[str, Any]] = None,
    ) -> Tuple[str, bool, Optional[str]]:
        """
        Renders template_body using safe sample values for previewing in the UI without sending SMS.
        Returns: (rendered_text, is_valid, error_message).
        """
        defaults = {
            "first_name": "Kofi",
            "amount": "100.00",
            "currency": "GHS",
            "contribution_type": "Tithe",
            "receipt_number": "HLC-1234567",
            "date": timezone.localdate().strftime("%d %b %Y"),
            "secure_link": "https://hlc.church/receipts/access/sample-token/",
        }
        if sample_context:
            defaults.update(sample_context)

        try:
            rendered = cls.render_template(template_body, defaults)
            return rendered, True, None
        except Exception as e:
            return "", False, str(e)


class NotificationService:
    """
    Handles the outbound notification lifecycle:
    Pending -> Sending -> Sent / Delivered (or Failed).
    Guarantees notification failure independence (Section 6.21).
    """

    @classmethod
    def create_and_send_notification(
        cls,
        recipient_phone: str,
        message: str,
        notification_type: str = NotificationType.THANK_YOU,
        related_record_type: str = "Contribution",
        related_record_id: Optional[Any] = None,
        recipient_name: Optional[str] = None,
        template: Optional[NotificationTemplate] = None,
        channel: str = "SMS",
        actor: Optional[User] = None,
    ) -> Notification:
        """
        Creates an outbound Notification, transitions through lifecycle states,
        dispatches to configured SMS provider, and records attempt history.
        """
        # 1. Create Pending Notification
        notification = Notification.objects.create(
            notification_type=notification_type,
            recipient_name=recipient_name,
            recipient_phone=recipient_phone,
            related_record_type=related_record_type,
            related_record_id=related_record_id,
            channel=channel,
            template=template,
            message=message,
            status=NotificationStatus.PENDING,
            attempt_count=0,
        )

        # 2. Transition to Sending
        notification.status = NotificationStatus.SENDING
        notification.save(update_fields=["status", "updated_at"])

        # 3. Dispatch to SMS Provider
        provider = get_sms_provider()
        attempt_number = 1
        try:
            sms_result = provider.send_sms(recipient_phone=recipient_phone, message=message)
        except Exception as exc:
            sms_result = SmsResult(success=False, status="FAILED", failure_reason=str(exc))

        # 4. Record Attempt Log
        NotificationAttempt.objects.create(
            notification=notification,
            attempt_number=attempt_number,
            provider_reference=sms_result.provider_reference,
            provider_response=sms_result.provider_response,
            status=sms_result.status,
            failure_code=sms_result.failure_code,
            failure_reason=sms_result.failure_reason,
        )

        # 5. Transition final state based on result
        now = timezone.now()
        if sms_result.success:
            notification.status = (NotificationStatus.DELIVERED if sms_result.status == "DELIVERED"
                                   else NotificationStatus.SENT)
            notification.sent_at = now
            notification.delivered_at = now if notification.status == NotificationStatus.DELIVERED else None
            notification.attempt_count = attempt_number
            notification.provider_reference = sms_result.provider_reference
            notification.failure_reason = None
            notification.save(update_fields=[
                "status", "sent_at", "delivered_at", "attempt_count",
                "provider_reference", "failure_reason", "updated_at"
            ])

            AuditService.log(
                action="NOTIFICATION_SENT",
                entity_type="Notification",
                entity_id=notification.id,
                user=actor,
                new_values={
                    "notification_type": notification.notification_type,
                    "recipient_phone": recipient_phone,
                    "channel": channel,
                    "status": notification.status,
                    "provider_reference": sms_result.provider_reference,
                },
                result=AuditResult.SUCCESS,
            )
            logger.info("Notification %s sent to %s via %s", notification.id, recipient_phone, provider.provider_code)
        else:
            notification.status = NotificationStatus.FAILED
            notification.attempt_count = attempt_number
            notification.failure_reason = sms_result.failure_reason
            notification.save(update_fields=[
                "status", "attempt_count", "failure_reason", "updated_at"
            ])

            AuditService.log(
                action="NOTIFICATION_FAILED",
                entity_type="Notification",
                entity_id=notification.id,
                user=actor,
                new_values={
                    "notification_type": notification.notification_type,
                    "recipient_phone": recipient_phone,
                    "status": NotificationStatus.FAILED,
                    "failure_code": sms_result.failure_code,
                    "failure_reason": sms_result.failure_reason,
                },
                result=AuditResult.FAILURE,
            )
            logger.warning(
                "Notification %s failed to send to %s: %s",
                notification.id,
                recipient_phone,
                sms_result.failure_reason,
            )

        return notification


class NotificationRetryService:
    """
    Manages retry lifecycle for failed outbound notifications (Section 6.17, 6.19).
    Enforces the 3-attempt limit. On 3rd failure, transitions to MANUAL_ACTION_REQUIRED
    and creates a Manual Action for operational review.
    """

    MAX_ATTEMPTS = 3

    @classmethod
    def retry_notification(
        cls,
        notification: Notification,
        max_attempts: int = MAX_ATTEMPTS,
        actor: Optional[User] = None,
    ) -> Notification:
        """
        Retries a failed notification up to the maximum attempts.
        If max_attempts reached, creates a Manual Action and alerts system administrators.
        """
        # Already succeeded
        if notification.status in [NotificationStatus.SENT, NotificationStatus.DELIVERED]:
            return notification

        # Already escalated
        if notification.attempt_count >= max_attempts:
            cls._escalate_to_manual_action(notification, actor)
            return notification

        # Transition to RETRYING
        notification.status = NotificationStatus.RETRYING
        notification.save(update_fields=["status", "updated_at"])

        next_attempt_number = notification.attempt_count + 1
        provider = get_sms_provider()
        try:
            sms_result = provider.send_sms(
                recipient_phone=notification.recipient_phone,
                message=notification.message,
            )
        except Exception as exc:
            sms_result = SmsResult(success=False, status="FAILED", failure_reason=str(exc))

        NotificationAttempt.objects.create(
            notification=notification,
            attempt_number=next_attempt_number,
            provider_reference=sms_result.provider_reference,
            provider_response=sms_result.provider_response,
            status=sms_result.status,
            failure_code=sms_result.failure_code,
            failure_reason=sms_result.failure_reason,
        )

        now = timezone.now()
        if sms_result.success:
            notification.status = (NotificationStatus.DELIVERED if sms_result.status == "DELIVERED"
                                   else NotificationStatus.SENT)
            notification.sent_at = notification.sent_at or now
            notification.delivered_at = now if notification.status == NotificationStatus.DELIVERED else None
            notification.attempt_count = next_attempt_number
            notification.provider_reference = sms_result.provider_reference
            notification.failure_reason = None
            notification.save(update_fields=[
                "status", "sent_at", "delivered_at", "attempt_count",
                "provider_reference", "failure_reason", "updated_at"
            ])

            AuditService.log(
                action="NOTIFICATION_RETRY_SUCCESS",
                entity_type="Notification",
                entity_id=notification.id,
                user=actor,
                new_values={
                    "attempt_number": next_attempt_number,
                    "status": notification.status,
                    "provider_reference": sms_result.provider_reference,
                },
                result=AuditResult.SUCCESS,
            )
            logger.info("Notification %s retry #%d succeeded", notification.id, next_attempt_number)
        else:
            notification.attempt_count = next_attempt_number
            notification.failure_reason = sms_result.failure_reason

            if next_attempt_number >= max_attempts:
                cls._escalate_to_manual_action(notification, actor)
            else:
                notification.status = NotificationStatus.FAILED
                notification.save(update_fields=["status", "attempt_count", "failure_reason", "updated_at"])
                AuditService.log(
                    action="NOTIFICATION_RETRY_FAILED",
                    entity_type="Notification",
                    entity_id=notification.id,
                    user=actor,
                    new_values={
                        "attempt_number": next_attempt_number,
                        "status": NotificationStatus.FAILED,
                        "failure_reason": sms_result.failure_reason,
                    },
                    result=AuditResult.FAILURE,
                )

        # Keep receipt history consistent with retries of its persisted SMS.
        if notification.related_record_type == "Receipt":
            from apps.receipts.models import ReceiptDeliveryRecord
            delivery = ReceiptDeliveryRecord.objects.filter(
                receipt_id=notification.related_record_id,
                recipient_phone=notification.recipient_phone,
            ).order_by("-created_at").first()
            if delivery:
                delivery.status = notification.status
                delivery.provider_reference = notification.provider_reference
                delivery.attempt_count = notification.attempt_count
                delivery.last_attempt_at = now
                delivery.delivered_at = notification.delivered_at
                delivery.failure_reason = notification.failure_reason
                delivery.save(update_fields=[
                    "status", "provider_reference", "attempt_count", "last_attempt_at",
                    "delivered_at", "failure_reason", "updated_at",
                ])
        return notification

    @classmethod
    def _escalate_to_manual_action(
        cls,
        notification: Notification,
        actor: Optional[User] = None,
    ) -> ManualAction:
        """
        Stops automatic retries and creates an operational Manual Action.
        """
        notification.status = NotificationStatus.MANUAL_ACTION_REQUIRED
        notification.save(update_fields=["status", "attempt_count", "failure_reason", "updated_at"])

        # Check if already created
        action_type = (ManualActionType.OTHER if notification.related_record_type == "Requisition"
                       else ManualActionType.THANK_YOU_NOTIFICATION_FAILED)
        action = ManualAction.objects.filter(
            related_record_type="Notification",
            related_record_id=notification.id,
            action_type=action_type,
        ).first()

        if not action:
            action = ManualAction.objects.create(
                action_type=action_type,
                related_record_type="Notification",
                related_record_id=notification.id,
                description=(
                    f"Outbound notification to {notification.recipient_phone} failed after "
                    f"{notification.attempt_count} attempts. Reason: {notification.failure_reason}"
                ),
                attempt_count=notification.attempt_count,
                priority=ManualActionPriority.MEDIUM,
                status=ManualActionStatus.OPEN,
            )

            AuditService.log(
                action="NOTIFICATION_MANUAL_ACTION_REQUIRED",
                entity_type="ManualAction",
                entity_id=action.id,
                user=actor,
                new_values={
                    "notification_id": str(notification.id),
                    "recipient_phone": notification.recipient_phone,
                    "attempt_count": notification.attempt_count,
                    "failure_reason": notification.failure_reason,
                },
                result=AuditResult.PARTIAL,
            )
            logger.warning(
                "Notification %s reached max retries (%d). Created ManualAction %s",
                notification.id,
                notification.attempt_count,
                action.id,
            )

        return action

    @classmethod
    def retry_all_failed_notifications(
        cls,
        max_attempts: int = MAX_ATTEMPTS,
        actor: Optional[User] = None,
    ) -> List[Notification]:
        """
        Batch retries all notifications eligible for retry.
        """
        failed_notifications = Notification.objects.filter(
            status__in=[NotificationStatus.FAILED, NotificationStatus.RETRYING],
            attempt_count__lt=max_attempts,
        ).order_by("created_at")

        processed = []
        for notif in failed_notifications:
            processed.append(cls.retry_notification(notif, max_attempts=max_attempts, actor=actor))
        return processed
