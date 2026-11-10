from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import pytest
from django.core.exceptions import PermissionDenied
from django.db import connection, close_old_connections, IntegrityError, transaction
from django.urls import reverse
from apps.accounts.models import Role, Permission, RolePermission, UserRole, User
from apps.requisitions.models import Requisition, RequisitionAssignment
from apps.requisitions.services import StaffService, RequesterService
from tests.test_m7_requester import VALID
import uuid


def reviewer(email):
    role = Role.objects.create(name=email)
    for code in ('requisition.view', 'requisition.review'):
        permission, _ = Permission.objects.get_or_create(code=code, defaults={'name': code})
        RolePermission.objects.create(role=role, permission=permission)
    user = User.objects.create_user(email=email, password=None)
    UserRole.objects.create(user=user, role=role)
    return user


def pending():
    req = RequesterService.create(uuid.uuid4())
    return RequesterService.save(RequesterService.token(req), VALID, submit=True)


def test_lock_permission_and_repeat(client):
    first, second = reviewer('first@example.com'), reviewer('second@example.com')
    req = pending()
    StaffService.start_working(first, req.pk)
    StaffService.start_working(first, req.pk)
    assert req.assignments.count() == 1
    with pytest.raises(PermissionDenied):
        StaffService.detail(second, req.pk)
    with pytest.raises(PermissionDenied):
        StaffService.start_working(second, req.pk)
    client.force_login(second)
    assert client.get(reverse('requisitions:detail', args=[req.pk])).status_code == 403
    assert client.get(reverse('requisitions:queue')).status_code == 200
    first.is_active = False
    first.save()
    with pytest.raises(PermissionDenied):
        StaffService.detail(first, req.pk)


def test_assignment_database_constraint():
    first, second = reviewer('first@example.com'), reviewer('second@example.com')
    req = pending()
    RequisitionAssignment.objects.create(requisition=req, assigned_to=first)
    with pytest.raises(IntegrityError), transaction.atomic():
        RequisitionAssignment.objects.create(requisition=req, assigned_to=second)


@pytest.mark.django_db(transaction=True)
def test_postgresql_concurrent_start_working():
    if connection.vendor != 'postgresql':
        pytest.skip('Requires PostgreSQL row locking; SQLite cannot validate this guarantee.')
    users = [reviewer('first@example.com'), reviewer('second@example.com')]
    req = pending()
    barrier = Barrier(2)
    def work(user_id):
        close_old_connections()
        try:
            actor = User.objects.get(pk=user_id)
            barrier.wait(timeout=10)
            try:
                StaffService.start_working(actor, req.pk)
                return True
            except PermissionDenied:
                return False
        finally:
            close_old_connections()
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(work, [u.pk for u in users])) == [False, True]
    assert req.assignments.filter(is_current=True).count() == 1


@pytest.mark.django_db(transaction=True)
def test_postgresql_concurrent_creation_and_submission():
    if connection.vendor != 'postgresql':
        pytest.skip('Requires PostgreSQL row locking.')
    key = uuid.uuid4()
    barrier = Barrier(2)
    def create(_):
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            return RequesterService.create(key).pk
        finally:
            close_old_connections()
    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(create, range(2)))
    assert ids[0] == ids[1]
    req = Requisition.objects.get(pk=ids[0])
    token = RequesterService.token(req)
    barrier = Barrier(2)
    def submit(_):
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            return RequesterService.save(token, VALID, submit=True).pk
        finally:
            close_old_connections()
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(submit, range(2))) == ids
    from apps.audit.models import AuditLog
    assert AuditLog.objects.filter(entity_id=req.pk, action='REQUISITION_SUBMITTED').count() == 1
