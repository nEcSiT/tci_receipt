"""Durable workflow notifications; delivery occurs only after the decision commits."""
import logging
from django.db import transaction
from apps.notifications.models import Notification, NotificationStatus, NotificationAttempt
from apps.notifications.services import NotificationRetryService

logger = logging.getLogger(__name__)


def dispatch(notification_id):
    # Lock the notification, never the financial record. Retrying cannot replay a
    # decision. A transport failure is retained for the existing retry mechanism.
    with transaction.atomic():
        item = Notification.objects.select_for_update().get(pk=notification_id)
        try:
            with transaction.atomic():
                NotificationRetryService.retry_notification(item)
        except Exception:
            item.refresh_from_db()
            item.status = NotificationStatus.FAILED
            item.attempt_count += 1
            item.failure_reason = 'SMS transport failed; retry required.'
            item.save(update_fields=['status', 'attempt_count', 'failure_reason', 'updated_at'])
            NotificationAttempt.objects.create(notification=item, attempt_number=item.attempt_count,
                status=NotificationStatus.FAILED, failure_reason=item.failure_reason)
            if item.attempt_count >= NotificationRetryService.MAX_ATTEMPTS:
                NotificationRetryService._escalate_to_manual_action(item)
            logger.warning('Requisition notification transport failed: %s', item.pk)


def queue_decision(req, decision, actor):
    if decision == 'APPROVED':
        message = f'{req.requisition_number} approved for GHS {req.approved_amount:.2f}. Awaiting disbursement.'
    else:
        message = f'{req.requisition_number} rejected. Reason: {req.latest_approval.reason}'
    item = Notification.objects.create(notification_type='REQUISITION_' + decision,
        recipient_phone=req.requester_phone, recipient_name=req.requester_name,
        related_record_type='Requisition', related_record_id=req.pk,
        message=message, status=NotificationStatus.PENDING)
    transaction.on_commit(lambda: dispatch(item.pk), robust=True)
    return item
