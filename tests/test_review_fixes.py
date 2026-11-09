from decimal import Decimal
from unittest.mock import Mock, patch

import pytest
from django.core.management import call_command
from django.urls import reverse

from apps.contributions.models import Contribution, ContributionType
from apps.members.models import Member
from apps.notifications.forms import NotificationTemplateForm
from apps.notifications.models import Notification, NotificationTemplate, NotificationType
from apps.notifications.providers.base import SmsResult
from apps.receipts.models import Receipt
from apps.receipts.services import ReceiptDeliveryService


@pytest.mark.parametrize('body', ['Dear {first_name', 'Thank you }'])
def test_malformed_template_is_form_error(body, seed_data):
    form = NotificationTemplateForm(data={
        'name': 'Invalid', 'notification_type': NotificationType.THANK_YOU,
        'template_body': body, 'is_active': True,
    })
    assert not form.is_valid()
    assert 'template_body' in form.errors


def test_seed_preserves_custom_wording_and_activation(seed_data):
    template = NotificationTemplate.objects.get(name='Tithe Thank You')
    template.template_body = 'Custom thanks, {first_name}'
    template.is_active = False
    template.save()
    call_command('seed_db')
    template.refresh_from_db()
    assert template.template_body == 'Custom thanks, {first_name}'
    assert template.is_active is False


@pytest.mark.parametrize('status', ['SENT', 'DELIVERED', 'FAILED', 'EXCEPTION'])
def test_receipt_uses_template_provider_and_truthful_status(seed_data, status):
    member = Member.objects.create(first_name='Grace', last_name='Ansah')
    contribution = Contribution.objects.create(
        member=member, contribution_type=ContributionType.objects.get(name='Tithe'),
        amount=Decimal('50.00'), payment_mode='CASH', entry_method='MANUAL',
    )
    receipt = Receipt.objects.create(contribution=contribution, pdf_storage_key='test.pdf')
    template = NotificationTemplate.objects.get(notification_type=NotificationType.RECEIPT, is_active=True)
    template.template_body = 'Custom receipt for {first_name}: {amount} {currency}'
    template.save()
    provider = Mock(provider_code='TEST')
    if status == 'EXCEPTION':
        provider.send_sms.side_effect = RuntimeError('Gateway unavailable')
    else:
        provider.send_sms.return_value = SmsResult(
            success=status != 'FAILED', status=status, provider_reference='provider-123',
            failure_reason='Rejected' if status == 'FAILED' else None,
        )
    with patch('apps.notifications.services.get_sms_provider', return_value=provider):
        delivery = ReceiptDeliveryService.dispatch_automatic_receipt(receipt, '0241234567')
    provider.send_sms.assert_called_once_with(
        recipient_phone='0241234567', message='Custom receipt for Grace: 50.00 GHS')
    notification = Notification.objects.get(related_record_id=receipt.id)
    expected = 'FAILED' if status == 'EXCEPTION' else status
    assert delivery.status == notification.status == expected
    assert bool(delivery.delivered_at) == (status == 'DELIVERED')
    assert bool(notification.delivered_at) == (status == 'DELIVERED')
    assert notification.attempt_count == delivery.attempt_count == 1
    assert notification.attempts.count() == 1
    assert notification.template == template


def test_admin_can_view_notification_history(client, system_admin):
    client.force_login(system_admin)
    assert client.get(reverse('notifications:notification_list')).status_code == 200


def test_retry_updates_receipt_delivery_without_claiming_delivery(seed_data):
    from apps.notifications.services import NotificationRetryService
    from apps.receipts.models import ReceiptDeliveryRecord
    member = Member.objects.create(first_name='Grace', last_name='Ansah')
    contribution = Contribution.objects.create(
        member=member, contribution_type=ContributionType.objects.get(name='Tithe'),
        amount=Decimal('50.00'), payment_mode='CASH', entry_method='MANUAL',
    )
    receipt = Receipt.objects.create(contribution=contribution, pdf_storage_key='test.pdf')
    notification = Notification.objects.create(
        notification_type=NotificationType.RECEIPT, related_record_type='Receipt',
        related_record_id=receipt.id, recipient_phone='0241234567',
        message='Saved receipt wording', status='FAILED', attempt_count=1,
    )
    delivery = ReceiptDeliveryRecord.objects.create(
        receipt=receipt, recipient_phone='0241234567', status='FAILED', attempt_count=1,
    )
    provider = Mock(provider_code='TEST')
    provider.send_sms.return_value = SmsResult(success=True, status='SENT', provider_reference='retry-123')
    with patch('apps.notifications.services.get_sms_provider', return_value=provider):
        NotificationRetryService.retry_notification(notification)
    delivery.refresh_from_db()
    assert delivery.status == 'SENT'
    assert delivery.delivered_at is None
    assert delivery.attempt_count == 2
    assert delivery.provider_reference == 'retry-123'
    provider.send_sms.assert_called_once_with(recipient_phone='0241234567', message='Saved receipt wording')


def test_seed_does_not_activate_default_over_custom_template(seed_data):
    original = NotificationTemplate.objects.get(name='Welfare Thank You')
    category = original.contribution_type
    original.delete()
    custom = NotificationTemplate.objects.create(
        name='Our welfare wording', notification_type=NotificationType.THANK_YOU,
        contribution_type=category, template_body='Thank you {first_name}', is_active=True,
    )
    call_command('seed_db')
    custom.refresh_from_db()
    assert custom.is_active
    assert not NotificationTemplate.objects.get(name='Welfare Thank You').is_active
