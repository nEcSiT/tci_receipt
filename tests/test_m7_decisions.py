from unittest.mock import patch
from decimal import Decimal
import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from apps.accounts.models import Permission, RolePermission
from apps.notifications.models import Notification
from apps.requisitions.services import StaffService
from apps.requisitions.notifications import dispatch
from tests.test_m7_assignment import reviewer, pending


def approver():
    actor = reviewer('approver@example.com')
    permission, _ = Permission.objects.get_or_create(code='requisition.approve', defaults={'name': 'Approve'})
    RolePermission.objects.create(role=actor.user_roles.first().role, permission=permission)
    return actor


def test_approval_separate_amounts_and_retry(django_capture_on_commit_callbacks):
    actor, req = approver(), pending()
    StaffService.start_working(actor, req.pk)
    with django_capture_on_commit_callbacks(execute=True):
        result = StaffService.decide(actor, req.pk, 'APPROVED', amount='100')
    assert result.amount_requested == Decimal('120.50')
    assert result.approved_amount == Decimal('100')
    assert result.disbursed_amount == 0
    assert result.status == 'PENDING_DISBURSEMENT'
    StaffService.decide(actor, req.pk, 'APPROVED', amount='100')
    assert req.approvals.count() == 1
    assert Notification.objects.filter(related_record_id=req.pk).count() == 1
    with pytest.raises(ValidationError):
        StaffService.decide(actor, req.pk, 'REJECTED', reason='Changed mind')


def test_rejection_reason_assignment_and_immutability():
    actor, req = approver(), pending()
    with pytest.raises(PermissionDenied):
        StaffService.decide(actor, req.pk, 'REJECTED', reason='No handler')
    StaffService.start_working(actor, req.pk)
    with pytest.raises(ValidationError):
        StaffService.decide(actor, req.pk, 'REJECTED', reason='   ')
    result = StaffService.decide(actor, req.pk, 'REJECTED', reason='Budget unavailable')
    assert result.status == 'REJECTED'
    result.description = 'Changed'
    with pytest.raises(ValidationError):
        result.save()


def test_sms_exception_does_not_reverse_decision(django_capture_on_commit_callbacks):
    actor, req = approver(), pending()
    StaffService.start_working(actor, req.pk)
    with patch('apps.notifications.services.get_sms_provider', side_effect=RuntimeError('offline')):
        with django_capture_on_commit_callbacks(execute=True):
            StaffService.decide(actor, req.pk, 'APPROVED', amount='100')
    req.refresh_from_db()
    assert req.status == 'PENDING_DISBURSEMENT'
    item = Notification.objects.get(related_record_id=req.pk)
    assert item.status == 'FAILED'
    dispatch(item.pk)
    assert req.approvals.count() == 1
    assert req.disbursements.count() == 0


def test_exhausted_workflow_sms_creates_generic_manual_action():
    from apps.notifications.models import ManualAction
    actor, req = approver(), pending()
    StaffService.start_working(actor, req.pk)
    StaffService.decide(actor, req.pk, 'APPROVED', amount='100')
    item = Notification.objects.get(related_record_id=req.pk)
    with patch('apps.notifications.services.get_sms_provider', side_effect=RuntimeError('offline')):
        for _ in range(4):
            dispatch(item.pk)
    item.refresh_from_db()
    assert item.status == 'MANUAL_ACTION_REQUIRED'
    assert item.attempt_count == 3
    assert ManualAction.objects.filter(related_record_id=item.pk, action_type='OTHER').count() == 1
    assert req.approvals.count() == 1


@pytest.mark.django_db(transaction=True)
def test_postgresql_concurrent_decisions():
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from django.db import connection, close_old_connections
    from apps.accounts.models import User
    if connection.vendor != 'postgresql':
        pytest.skip('Requires PostgreSQL row locking.')
    actor, req = approver(), pending()
    StaffService.start_working(actor, req.pk)
    barrier = Barrier(2)
    def work(decision):
        close_old_connections()
        try:
            user = User.objects.get(pk=actor.pk)
            barrier.wait(timeout=10)
            try:
                StaffService.decide(user, req.pk, decision, amount='100', reason='Decision')
                return True
            except ValidationError:
                return False
        finally:
            close_old_connections()
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(work, ['APPROVED', 'REJECTED'])) == [False, True]
    assert req.approvals.count() == 1
    assert Notification.objects.filter(related_record_id=req.pk).count() == 1
