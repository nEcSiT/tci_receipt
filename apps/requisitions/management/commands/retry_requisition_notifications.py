from django.core.management.base import BaseCommand
from apps.notifications.models import Notification, NotificationStatus
from apps.requisitions.notifications import dispatch


class Command(BaseCommand):
    help = 'Deliver pending/retryable requisition SMS without repeating financial operations.'

    def handle(self, *args, **options):
        ids = Notification.objects.filter(related_record_type='Requisition', status__in=[
            NotificationStatus.PENDING, NotificationStatus.FAILED, NotificationStatus.RETRYING
        ]).values_list('pk', flat=True)
        for notification_id in ids.iterator():
            dispatch(notification_id)
