import logging
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple
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

logger = logging.getLogger(__name__)


class SafeDict(dict):
    """Safely returns missing keys formatted as {key} instead of raising KeyError."""
    def __missing__(self, key: str) -> str:
        return f"{{{key}}}"


class NotificationTemplateService:
    """
    Manages configurable notification templates supporting variable substitution:
    {first_name}, {amount}, {currency}, {contribution_type}, {receipt_number}, {date}, {secure_link}.
    """

    @classmethod
    def render_template(cls, template_body: str, context: Dict[str, Any]) -> str:
        safe_ctx = SafeDict()
        for k, v in context.items():
            safe_ctx[k] = "" if v is None else str(v)
        return template_body.format_map(safe_ctx)

    @classmethod
    def get_thank_you_template(
        cls,
        contribution_type: Optional[ContributionType] = None,
    ) -> Optional[NotificationTemplate]:
        """
        Retrieves the most specific active Thank You template for the given contribution type.
        Falls back to the general Thank You template if type-specific template is not found.
        """
        if contribution_type:
            type_template = NotificationTemplate.objects.filter(
                notification_type=NotificationType.THANK_YOU,
                contribution_type=contribution_type,
                is_active=True,
            ).first()
            if type_template:
                return type_template

        # Fallback to general template (contribution_type is None)
        return NotificationTemplate.objects.filter(
            notification_type=NotificationType.THANK_YOU,
            contribution_type__isnull=True,
            is_active=True,
        ).first()

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
            if receipt_number and receipt_number not in message:
                message += f" Receipt: {receipt_number}."
            if secure_link and secure_link not in message:
                message += f" View: {secure_link}"
        else:
            # Fallback per Specification 6.14
            # "Thank you, Nicholas, for your contribution of GHS 100 towards the Building Project. God bless you."
            message = (
                f"Thank you, {display_name}, for your contribution of {currency} {amount:.2f} "
                f"towards the {contribution_type_name}. God bless you."
            )
            if receipt_number:
                message += f" Receipt: {receipt_number}."
            if secure_link:
                message += f" View: {secure_link}"

        return message, template


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
        sms_result = provider.send_sms(recipient_phone=recipient_phone, message=message)

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
            notification.status = NotificationStatus.DELIVERED
            notification.sent_at = now
            notification.delivered_at = now
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
        sms_result = provider.send_sms(
            recipient_phone=notification.recipient_phone,
            message=notification.message,
        )

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
            notification.status = NotificationStatus.DELIVERED
            notification.sent_at = notification.sent_at or now
            notification.delivered_at = now
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
                    "status": NotificationStatus.DELIVERED,
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
        action = ManualAction.objects.filter(
            related_record_type="Notification",
            related_record_id=notification.id,
            action_type=ManualActionType.THANK_YOU_NOTIFICATION_FAILED,
        ).first()

        if not action:
            action = ManualAction.objects.create(
                action_type=ManualActionType.THANK_YOU_NOTIFICATION_FAILED,
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
