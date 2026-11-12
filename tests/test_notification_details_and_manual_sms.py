import uuid
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier
import pytest
from django.core.exceptions import PermissionDenied
from django.db import connection, transaction
from django.test import Client
from django.urls import reverse

from apps.accounts.models import Permission, Role, RolePermission, User, UserRole
from apps.contributions.models import Contribution, ContributionType, EntryMethod, PaymentMode
from apps.contributions.providers.base import PaymentStatus
from apps.contributions.services import AutomaticPaymentService, ContributionService
from apps.members.models import Member, MemberPhoneNumber, MemberStatus
from apps.notifications.models import (
    Notification,
    NotificationAttempt,
    NotificationStatus,
    NotificationTemplate,
    NotificationType,
)
from apps.notifications.providers.factory import set_sms_provider_instance
from apps.notifications.providers.mock import MockSmsProvider
from apps.notifications.services import NotificationRetryService, NotificationTemplateService
from apps.receipts.models import Receipt, ReceiptStatus
from apps.receipts.services import ReceiptService


@pytest.fixture(autouse=True)
def setup_mock_sms():
    mock_sms = MockSmsProvider()
    set_sms_provider_instance(mock_sms)
    yield mock_sms
    mock_sms.clear()
    set_sms_provider_instance(None)


@pytest.fixture
def notification_viewer(seed_data):
    role = Role.objects.create(name="Notification Viewer")
    perm = Permission.objects.get(code="notification.view")
    RolePermission.objects.create(role=role, permission=perm)
    user = User.objects.create_user(
        email="viewer@tcihlc.org",
        password="TestPassword123!",
        first_name="Notice",
        last_name="Viewer",
    )
    UserRole.objects.create(user=user, role=role)
    return user


@pytest.fixture
def unprivileged_user(seed_data):
    role = Role.objects.create(name="Plain User")
    user = User.objects.create_user(
        email="plain@tcihlc.org",
        password="TestPassword123!",
        first_name="Plain",
        last_name="User",
    )
    UserRole.objects.create(user=user, role=role)
    return user


@pytest.fixture
def member_with_primary_phone(seed_data):
    member = Member.objects.create(
        first_name="Nicholas",
        last_name="Appiah",
        email="nicholas@tcihlc.org",
        status=MemberStatus.ACTIVE,
    )
    MemberPhoneNumber.objects.create(
        member=member,
        phone_number="0241234567",
        is_primary=True,
        is_active=True,
    )
    return member


@pytest.fixture
def manual_contribution(seed_data, system_admin, member_with_primary_phone):
    c_type = ContributionType.objects.get(name="Tithe")
    return ContributionService.record_manual_contribution(
        recorder=system_admin,
        member=member_with_primary_phone,
        contribution_type=c_type,
        amount=Decimal("150.00"),
        payment_mode=PaymentMode.CASH,
    )


# 1. Authorized users can open a notification and read its exact saved message
def test_01_authorized_users_can_open_notification_and_read_exact_saved_message(
    seed_data, notification_viewer
):
    notif = Notification.objects.create(
        notification_type=NotificationType.THANK_YOU,
        recipient_name="Sister Grace",
        recipient_phone="0240001122",
        message="Dear Grace,\nThank you for giving 200.00 GHS!\nGod bless & keep you <safely>.",
        status=NotificationStatus.DELIVERED,
        provider_reference="SMS-REF-9988",
        attempt_count=1,
    )
    NotificationAttempt.objects.create(
        notification=notif,
        attempt_number=1,
        status="DELIVERED",
        provider_reference="SMS-REF-9988",
    )

    client = Client()
    client.force_login(notification_viewer)
    resp = client.get(reverse("notifications:notification_detail", args=[notif.id]))
    assert resp.status_code == 200

    content = resp.content.decode("utf-8")
    assert "Dear Grace," in content
    assert "Thank you for giving 200.00 GHS!" in content
    # Verify escaped HTML
    assert "&lt;safely&gt;" in content
    assert "0240001122" in content
    assert "Sister Grace" in content
    assert "SMS-REF-9988" in content
    assert "Delivered" in content
    assert "Back to notifications" in content


# 2. Unauthorized users cannot access notification detail URLs
def test_02_unauthorized_users_cannot_access_notification_detail_urls(
    seed_data, unprivileged_user
):
    notif = Notification.objects.create(
        notification_type=NotificationType.THANK_YOU,
        recipient_phone="0240001122",
        message="Secret message",
        status=NotificationStatus.SENT,
    )

    client = Client()
    # Unauthenticated
    resp = client.get(reverse("notifications:notification_detail", args=[notif.id]))
    assert resp.status_code in [302, 403]
    if resp.status_code == 302:
        assert "/login/" in resp.url

    # Authenticated without permission
    client.force_login(unprivileged_user)
    resp = client.get(reverse("notifications:notification_detail", args=[notif.id]))
    assert resp.status_code == 403


# 3. Changing a template does not change historical message text
def test_03_changing_template_does_not_change_historical_message_text(
    seed_data, system_admin, notification_viewer
):
    c_type = ContributionType.objects.get(name="Tithe")
    template = NotificationTemplate.objects.filter(
        notification_type=NotificationType.THANK_YOU,
        contribution_type=c_type,
        is_active=True,
    ).first()

    original_text = "Historical verbatim thank-you note from 2026-10-01."
    notif = Notification.objects.create(
        notification_type=NotificationType.THANK_YOU,
        recipient_phone="0241112233",
        template=template,
        message=original_text,
        status=NotificationStatus.DELIVERED,
    )

    # Change template body in database
    template.template_body = "Completely updated new template wording with {first_name}"
    template.save()

    client = Client()
    client.force_login(notification_viewer)
    resp = client.get(reverse("notifications:notification_detail", args=[notif.id]))
    assert resp.status_code == 200
    content = resp.content.decode("utf-8")
    assert original_text in content
    assert "Completely updated new template wording" not in content


# 4. Viewing a notification causes no send or retry
def test_04_viewing_notification_causes_no_send_or_retry(
    seed_data, notification_viewer, setup_mock_sms
):
    notif = Notification.objects.create(
        notification_type=NotificationType.THANK_YOU,
        recipient_phone="0241112233",
        message="Pending notice",
        status=NotificationStatus.PENDING,
        attempt_count=0,
    )

    setup_mock_sms.clear()
    client = Client()
    client.force_login(notification_viewer)

    for _ in range(3):
        resp = client.get(reverse("notifications:notification_detail", args=[notif.id]))
        assert resp.status_code == 200

    notif.refresh_from_db()
    assert notif.status == NotificationStatus.PENDING
    assert notif.attempt_count == 0
    assert len(setup_mock_sms.sent_messages) == 0


# 5. Successful manual receipt generation creates one personalized thank-you SMS using the correct category template
def test_05_successful_manual_receipt_generation_creates_personalized_thank_you_sms(
    seed_data, system_admin, member_with_primary_phone, setup_mock_sms, django_capture_on_commit_callbacks
):
    # Tithe test
    tithe_type = ContributionType.objects.get(name="Tithe")
    c1 = ContributionService.record_manual_contribution(
        recorder=system_admin,
        member=member_with_primary_phone,
        contribution_type=tithe_type,
        amount=Decimal("300.00"),
        payment_mode=PaymentMode.CASH,
    )

    with django_capture_on_commit_callbacks(execute=True):
        receipt1 = ReceiptService.generate_receipt(c1, generated_by_user=system_admin)
    assert receipt1 is not None

    notifs = Notification.objects.filter(
        related_record_type="Contribution",
        related_record_id=c1.id,
        notification_type=NotificationType.THANK_YOU,
    )
    assert notifs.count() == 1
    n1 = notifs.first()
    assert n1.recipient_phone == "0241234567"
    assert "Dear Nicholas" in n1.message
    assert "Tithe" in n1.message
    assert receipt1.receipt_number in n1.message
    assert n1.status in [NotificationStatus.SENT, NotificationStatus.DELIVERED]
    assert len(setup_mock_sms.sent_messages) == 1
    assert setup_mock_sms.sent_messages[0]["recipient_phone"] == "0241234567"

    # Offering test
    setup_mock_sms.clear()
    offering_type = ContributionType.objects.get(name="Offering")
    c2 = ContributionService.record_manual_contribution(
        recorder=system_admin,
        member=member_with_primary_phone,
        contribution_type=offering_type,
        amount=Decimal("75.00"),
        payment_mode=PaymentMode.CASH,
    )
    with django_capture_on_commit_callbacks(execute=True):
        receipt2 = ReceiptService.generate_receipt(c2, generated_by_user=system_admin)
    n2 = Notification.objects.get(
        related_record_type="Contribution",
        related_record_id=c2.id,
        notification_type=NotificationType.THANK_YOU,
    )
    assert "Dear Nicholas" in n2.message
    assert "Offering" in n2.message
    assert receipt2.receipt_number in n2.message


# 6. Repeated and concurrent generation requests do not create duplicate notifications
def test_06_repeated_generation_requests_do_not_create_duplicate_notifications(
    seed_data, manual_contribution, system_admin, setup_mock_sms, django_capture_on_commit_callbacks
):
    # First generation
    with django_capture_on_commit_callbacks(execute=True):
        r1 = ReceiptService.generate_receipt(manual_contribution, generated_by_user=system_admin)
    # Repeated calls
    with django_capture_on_commit_callbacks(execute=True):
        r2 = ReceiptService.generate_receipt(manual_contribution, generated_by_user=system_admin)
    with django_capture_on_commit_callbacks(execute=True):
        r3 = ReceiptService.generate_receipt(manual_contribution, generated_by_user=system_admin)

    assert r1.id == r2.id == r3.id

    notifs = Notification.objects.filter(
        related_record_type="Contribution",
        related_record_id=manual_contribution.id,
        notification_type=NotificationType.THANK_YOU,
    )
    assert notifs.count() == 1
    assert len(setup_mock_sms.sent_messages) == 1


# 7. Transaction rollback prevents dispatch
def test_07_transaction_rollback_prevents_dispatch(
    seed_data, manual_contribution, system_admin, setup_mock_sms
):
    setup_mock_sms.clear()

    with pytest.raises(RuntimeError):
        with transaction.atomic():
            ReceiptService.generate_receipt(manual_contribution, generated_by_user=system_admin)
            raise RuntimeError("Simulated transaction rollback!")

    # Verify no receipt, no notification, no SMS dispatch
    assert not Receipt.objects.filter(contribution=manual_contribution).exists()
    assert not Notification.objects.filter(
        related_record_type="Contribution",
        related_record_id=manual_contribution.id,
    ).exists()
    assert len(setup_mock_sms.sent_messages) == 0


# 8. Missing phone, gateway rejection and gateway exception preserve the contribution and receipt
def test_08_missing_phone_preserves_contribution_and_receipt(
    seed_data, system_admin, setup_mock_sms
):
    # Member with NO active phones
    member_no_phone = Member.objects.create(
        first_name="NoPhone",
        last_name="Member",
        status=MemberStatus.ACTIVE,
    )
    c_type = ContributionType.objects.get(name="Tithe")
    c = ContributionService.record_manual_contribution(
        recorder=system_admin,
        member=member_no_phone,
        contribution_type=c_type,
        amount=Decimal("50.00"),
        payment_mode=PaymentMode.CASH,
    )

    setup_mock_sms.clear()
    receipt = ReceiptService.generate_receipt(c, generated_by_user=system_admin)

    assert receipt is not None
    assert receipt.status == ReceiptStatus.ACTIVE
    c.refresh_from_db()
    assert c.status == "CONFIRMED"

    # Notification was created with FAILED status
    notif = Notification.objects.get(
        related_record_type="Contribution",
        related_record_id=c.id,
        notification_type=NotificationType.THANK_YOU,
    )
    assert notif.status == NotificationStatus.FAILED
    assert notif.attempt_count == 1
    assert "No valid recipient phone" in notif.failure_reason
    assert len(setup_mock_sms.sent_messages) == 0


def test_08_gateway_rejection_preserves_contribution_and_receipt(
    seed_data, manual_contribution, system_admin, setup_mock_sms, django_capture_on_commit_callbacks
):
    # Configure mock provider to reject this phone
    setup_mock_sms.set_phone_to_fail("0241234567")

    with django_capture_on_commit_callbacks(execute=True):
        receipt = ReceiptService.generate_receipt(manual_contribution, generated_by_user=system_admin)
    assert receipt is not None
    assert receipt.status == ReceiptStatus.ACTIVE
    manual_contribution.refresh_from_db()
    assert manual_contribution.status == "CONFIRMED"

    notif = Notification.objects.get(
        related_record_type="Contribution",
        related_record_id=manual_contribution.id,
        notification_type=NotificationType.THANK_YOU,
    )
    assert notif.status in [NotificationStatus.FAILED, NotificationStatus.MANUAL_ACTION_REQUIRED]
    assert notif.attempt_count >= 1
    assert notif.failure_reason is not None


def test_08_gateway_exception_preserves_contribution_and_receipt(
    seed_data, manual_contribution, system_admin, setup_mock_sms, monkeypatch, django_capture_on_commit_callbacks
):
    def broken_send(recipient_phone, message):
        raise ConnectionResetError("Fatal socket drop during SMS send")

    monkeypatch.setattr(setup_mock_sms, "send_sms", broken_send)

    with django_capture_on_commit_callbacks(execute=True):
        receipt = ReceiptService.generate_receipt(manual_contribution, generated_by_user=system_admin)
    assert receipt is not None
    assert receipt.status == ReceiptStatus.ACTIVE
    manual_contribution.refresh_from_db()
    assert manual_contribution.status == "CONFIRMED"

    notif = Notification.objects.get(
        related_record_type="Contribution",
        related_record_id=manual_contribution.id,
        notification_type=NotificationType.THANK_YOU,
    )
    assert notif.status in [NotificationStatus.FAILED, NotificationStatus.MANUAL_ACTION_REQUIRED]
    assert "Fatal socket drop" in str(notif.failure_reason)


# 9. Retry uses the saved wording and updates status correctly
def test_09_retry_uses_saved_wording_and_updates_status(
    seed_data, manual_contribution, system_admin, setup_mock_sms, django_capture_on_commit_callbacks
):
    # Fail first 2 attempts (initial dispatch + first automatic retry)
    setup_mock_sms.fail_next_n_times_for_phone("0241234567", 2)

    with django_capture_on_commit_callbacks(execute=True):
        receipt = ReceiptService.generate_receipt(manual_contribution, generated_by_user=system_admin)
    notif = Notification.objects.get(
        related_record_type="Contribution",
        related_record_id=manual_contribution.id,
        notification_type=NotificationType.THANK_YOU,
    )
    assert notif.status == NotificationStatus.FAILED
    assert notif.attempt_count == 2
    saved_wording = notif.message

    # Tamper with the active template in the DB
    NotificationTemplate.objects.filter(is_active=True).update(
        template_body="Tampered template body that should NOT appear in retry"
    )

    # Perform subsequent retry
    setup_mock_sms.clear()
    NotificationRetryService.retry_notification(notif, actor=system_admin)

    notif.refresh_from_db()
    assert notif.status in [NotificationStatus.SENT, NotificationStatus.DELIVERED]
    assert len(setup_mock_sms.sent_messages) == 1
    assert setup_mock_sms.sent_messages[0]["message"] == saved_wording
    assert "Tampered template" not in setup_mock_sms.sent_messages[0]["message"]


# 10. Existing automatic receipt and thank-you behavior remains intact
def test_10_existing_automatic_receipt_and_thank_you_behavior_remains_intact(
    seed_data, member_with_primary_phone, setup_mock_sms
):
    setup_mock_sms.clear()
    ref = f"AUTO-TX-{uuid.uuid4().hex[:8]}"

    res = AutomaticPaymentService.process_payment_confirmation(
        provider_reference=ref,
        transaction_phone="0241234567",
        amount=Decimal("120.00"),
        contribution_type_name="Tithe",
        status=PaymentStatus.SUCCESSFUL,
        provider_name="MTN Mobile Money",
    )
    assert res["status"] == "SUCCESSFUL"
    assert res["contribution"].entry_method == EntryMethod.AUTOMATIC

    # Verify both RECEIPT delivery record and THANK_YOU notification were created
    receipt_notif = Notification.objects.filter(
        related_record_type="Receipt",
        related_record_id=res["receipt"].id,
        notification_type=NotificationType.RECEIPT,
    ).first()
    assert receipt_notif is not None

    thank_you_notif = Notification.objects.filter(
        related_record_type="Contribution",
        related_record_id=res["contribution"].id,
        notification_type=NotificationType.THANK_YOU,
    ).first()
    assert thank_you_notif is not None
    assert "Dear Nicholas" in thank_you_notif.message


# 11. List view status badges and View message action links with query persistence
def test_11_list_view_status_badges_and_action_links(seed_data, notification_viewer):
    notif = Notification.objects.create(
        notification_type=NotificationType.THANK_YOU,
        recipient_phone="0249991111",
        message="Test list links",
        status=NotificationStatus.DELIVERED,
    )

    client = Client()
    client.force_login(notification_viewer)
    resp = client.get("/notifications/?q=0249991111&type=THANK_YOU")
    assert resp.status_code == 200

    content = resp.content.decode("utf-8")
    detail_url = reverse("notifications:notification_detail", args=[notif.id])
    assert detail_url in content
    assert "View message" in content
    # Assert query parameters are preserved in links
    assert "q=0249991111" in content


# 12. Related record links respect permissions
def test_12_related_record_links_respect_permissions(
    seed_data, manual_contribution, system_admin, notification_viewer
):
    receipt = ReceiptService.generate_receipt(manual_contribution, generated_by_user=system_admin)
    notif = Notification.objects.get(
        related_record_type="Contribution",
        related_record_id=manual_contribution.id,
    )

    client = Client()

    # Viewer has notification.view but NOT contribution.view
    client.force_login(notification_viewer)
    resp = client.get(reverse("notifications:notification_detail", args=[notif.id]))
    assert resp.status_code == 200
    content = resp.content.decode("utf-8")
    assert manual_contribution.contribution_number in content
    # Should show restricted access note instead of clickable link
    assert f"/contributions/{manual_contribution.id}/" not in content
    assert "Restricted access" in content

    # System administrator has full access
    client.force_login(system_admin)
    resp_admin = client.get(reverse("notifications:notification_detail", args=[notif.id]))
    assert resp_admin.status_code == 200
    content_admin = resp_admin.content.decode("utf-8")
    assert f"/contributions/{manual_contribution.id}/" in content_admin
    assert f"/receipts/{receipt.id}/" in content_admin


# 13. Deterministic fallback to secondary active phone
def test_13_deterministic_fallback_to_secondary_phone(seed_data, system_admin, setup_mock_sms):
    member = Member.objects.create(
        first_name="Kofi",
        last_name="Mensah",
        status=MemberStatus.ACTIVE,
    )
    # Two active phones, neither is primary
    p1 = MemberPhoneNumber.objects.create(
        member=member,
        phone_number="0241110001",
        is_primary=False,
        is_active=True,
    )
    p2 = MemberPhoneNumber.objects.create(
        member=member,
        phone_number="0241110002",
        is_primary=False,
        is_active=True,
    )

    c_type = ContributionType.objects.get(name="Tithe")
    c = ContributionService.record_manual_contribution(
        recorder=system_admin,
        member=member,
        contribution_type=c_type,
        amount=Decimal("100.00"),
        payment_mode=PaymentMode.CASH,
    )

    setup_mock_sms.clear()
    ReceiptService.generate_receipt(c, generated_by_user=system_admin)

    notif = Notification.objects.get(
        related_record_type="Contribution",
        related_record_id=c.id,
    )
    # Earliest created phone (p1) is deterministically selected
    assert notif.recipient_phone == p1.phone_number


# 14. PostgreSQL concurrent receipt and notification creation validation
@pytest.mark.django_db(transaction=True)
def test_14_postgresql_concurrent_receipt_and_notification(seed_data, member_with_primary_phone, system_admin):
    if connection.vendor != "postgresql":
        pytest.skip("Requires PostgreSQL row locking; SQLite cannot validate concurrency across threads.")

    c_type = ContributionType.objects.get(name="Tithe")
    c = ContributionService.record_manual_contribution(
        recorder=system_admin,
        member=member_with_primary_phone,
        contribution_type=c_type,
        amount=Decimal("150.00"),
        payment_mode=PaymentMode.CASH,
    )

    barrier = Barrier(4)

    def worker():
        barrier.wait()
        try:
            return ReceiptService.generate_receipt(c, generated_by_user=system_admin)
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(worker) for _ in range(4)]
        receipts = [f.result() for f in futures]

    # All threads receive the same receipt
    receipt_ids = {r.id for r in receipts}
    assert len(receipt_ids) == 1

    # Exactly one receipt in DB
    assert Receipt.objects.filter(contribution=c).count() == 1

    # Exactly one THANK_YOU notification in DB
    assert (
        Notification.objects.filter(
            related_record_type="Contribution",
            related_record_id=c.id,
            notification_type=NotificationType.THANK_YOU,
        ).count()
        == 1
    )
