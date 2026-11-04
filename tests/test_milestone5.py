import datetime
from decimal import Decimal
import re
import pytest
from django.core.exceptions import ValidationError, PermissionDenied
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Role, Permission, RolePermission, User, UserRole
from apps.audit.models import AuditLog
from apps.contributions.models import (
    Contribution,
    ContributionType,
    PaymentTransaction,
    PaymentMode,
    EntryMethod,
    ContributionStatus,
    PaymentTransactionStatus,
)
from apps.contributions.services import ContributionService
from apps.core.services.storage import get_storage_service
from apps.members.models import Member, MemberPhoneNumber, TemporaryContributor, MemberStatus
from apps.notifications.models import Notification, NotificationType, NotificationStatus
from apps.receipts.models import Receipt, ReceiptStatus, ReceiptEdit, ReceiptDeliveryRecord
from apps.receipts.services import ReceiptService, ReceiptPdfService, ReceiptDeliveryService


# =========================================================================
# FIXTURES
# =========================================================================

@pytest.fixture
def tithe_type(seed_data):
    return ContributionType.objects.get(name="Tithe")


@pytest.fixture
def thanksgiving_type(seed_data):
    return ContributionType.objects.get(name="Thanksgiving")


@pytest.fixture
def building_type(seed_data):
    return ContributionType.objects.get(name="Building Project")


@pytest.fixture
def other_type(seed_data):
    return ContributionType.objects.get(name="Other")


@pytest.fixture
def active_member(db):
    member = Member.objects.create(
        first_name="Kwame",
        last_name="Mensah",
        email="kwame.mensah@tcihlc.org",
        status=MemberStatus.ACTIVE,
    )
    MemberPhoneNumber.objects.create(
        member=member,
        phone_number="+233241112233",
        provider="MTN",
        is_primary=True,
        is_active=True,
    )
    return member


@pytest.fixture
def receipt_creator(seed_data):
    """System User with contribution & receipt manage permissions."""
    role = Role.objects.create(name="Receipt Creator Role", description="Creates and manages own receipts")
    for code in ["contribution.create", "contribution.view", "receipt.view_own", "receipt.edit_own", "receipt.delete_own"]:
        perm = Permission.objects.get(code=code)
        RolePermission.objects.create(role=role, permission=perm)

    user = User.objects.create_user(
        email="receipt.creator@tcihlc.org",
        password="ValidPassword123!",
        first_name="Abena",
        last_name="Osei",
    )
    UserRole.objects.create(user=user, role=role)
    return user


@pytest.fixture
def other_system_user(seed_data):
    """Another System User with receipt edit permissions to test creator-only isolation."""
    role = Role.objects.create(name="Secondary User Role", description="Another staff member")
    for code in ["contribution.view", "receipt.view_own", "receipt.edit_own", "receipt.delete_own"]:
        perm = Permission.objects.get(code=code)
        RolePermission.objects.create(role=role, permission=perm)

    user = User.objects.create_user(
        email="other.user@tcihlc.org",
        password="ValidPassword123!",
        first_name="Kojo",
        last_name="Antwi",
    )
    UserRole.objects.create(user=user, role=role)
    return user


@pytest.fixture
def manual_contribution(active_member, tithe_type, receipt_creator):
    return ContributionService.record_manual_contribution(
        recorder=receipt_creator,
        member=active_member,
        contribution_type=tithe_type,
        amount=Decimal("350.00"),
        payment_mode=PaymentMode.CASH,
        reference_number="REF-M5-001",
        contribution_date=datetime.date(2026, 9, 20),
    )


@pytest.fixture
def auto_contribution(active_member, thanksgiving_type):
    txn = PaymentTransaction.objects.create(
        provider="HUBTEL",
        provider_reference="TXN-HUB-998877",
        transaction_phone="+233241112233",
        amount=Decimal("500.00"),
        currency="GHS",
        status=PaymentTransactionStatus.SUCCESSFUL,
        provider_data={"provider": "HUBTEL", "ref": "TXN-HUB-998877"},
    )
    return Contribution.objects.create(
        member=active_member,
        contribution_type=thanksgiving_type,
        amount=Decimal("500.00"),
        currency="GHS",
        payment_mode=PaymentMode.MOBILE_MONEY,
        entry_method=EntryMethod.AUTOMATIC,
        payment_transaction=txn,
        status=ContributionStatus.CONFIRMED,
        contribution_date=datetime.date(2026, 9, 22),
    )


# =========================================================================
# TEST SUITE: MILESTONE 5 — RECEIPT MANAGEMENT
# =========================================================================

def test_m5_bt_001_receipt_number_generation_and_format(manual_contribution, receipt_creator):
    """
    M5-BT-001: Receipt generation creates unique HLC-XXXXXXX (strictly 7 digits).
    """
    receipt = ReceiptService.generate_receipt(
        contribution=manual_contribution,
        generated_by_user=receipt_creator,
        actor=receipt_creator,
    )
    assert receipt.receipt_number.startswith("HLC-")
    assert len(receipt.receipt_number) == 11
    assert re.match(r"^HLC-\d{7}$", receipt.receipt_number)
    assert receipt.status == ReceiptStatus.ACTIVE
    assert receipt.generated_by_user == receipt_creator
    assert not receipt.generated_by_system


def test_m5_bt_002_receipt_one_to_one_and_idempotency(manual_contribution, receipt_creator):
    """
    M5-BT-002: A contribution has at most one receipt; repeated generation returns existing record.
    """
    r1 = ReceiptService.generate_receipt(manual_contribution, generated_by_user=receipt_creator)
    r2 = ReceiptService.generate_receipt(manual_contribution, generated_by_user=receipt_creator)
    assert r1.id == r2.id
    assert r1.receipt_number == r2.receipt_number


def test_m5_bt_003_automatic_receipt_properties(auto_contribution):
    """
    M5-BT-003: Automatic contributions generate system receipts with generated_by_system=True.
    """
    receipt = ReceiptService.generate_receipt(auto_contribution, is_system=True)
    assert receipt.generated_by_system is True
    assert receipt.generated_by_user is None
    assert receipt.generated_by_display == "HLC/System"


def test_m5_bt_004_automatic_receipt_immutability(auto_contribution, system_admin):
    """
    M5-BT-004: Automatic receipts are strictly immutable; attempts to modify fail validation.
    """
    receipt = ReceiptService.generate_receipt(auto_contribution, is_system=True)

    # Direct model modification attempt
    receipt.pdf_storage_key = "receipts/tampered/hacked.pdf"
    with pytest.raises(ValidationError, match="Automatic receipts are finalized and immutable"):
        receipt.save()

    # Service edit attempt
    with pytest.raises(ValidationError, match="Automatic receipts are finalized and immutable"):
        ReceiptService.edit_manual_receipt(
            receipt=receipt,
            user=system_admin,
            changed_fields={"amount": Decimal("600.00")},
            reason="Attempted admin edit",
        )


def test_m5_bt_005_automatic_receipt_cannot_be_deleted(auto_contribution, system_admin):
    """
    M5-BT-005: Automatic receipts cannot be deleted by anyone, including the System Administrator.
    """
    receipt = ReceiptService.generate_receipt(auto_contribution, is_system=True)

    with pytest.raises(ValidationError, match="Automatic receipts are immutable and cannot be deleted"):
        receipt.soft_delete(user=system_admin)

    with pytest.raises(ValidationError, match="Automatic receipts are immutable and cannot be deleted"):
        ReceiptService.delete_manual_receipt(receipt, user=system_admin, reason="Admin deletion")


def test_m5_bt_006_manual_receipt_edit_creator_only(manual_contribution, receipt_creator, other_system_user, building_type):
    """
    M5-BT-006: Manual receipt edits are creator-only. Other users cannot edit even if permitted.
    """
    receipt = ReceiptService.generate_receipt(manual_contribution, generated_by_user=receipt_creator)

    # Non-creator attempt is blocked with PermissionDenied
    with pytest.raises(PermissionDenied, match="Only the creator of a manual receipt may edit it"):
        ReceiptService.edit_manual_receipt(
            receipt=receipt,
            user=other_system_user,
            changed_fields={"amount": Decimal("400.00")},
            reason="Non-creator attempt",
        )

    # Creator edit succeeds
    updated = ReceiptService.edit_manual_receipt(
        receipt=receipt,
        user=receipt_creator,
        changed_fields={
            "amount": Decimal("400.00"),
            "contribution_type_id": str(building_type.id),
        },
        reason="Member increased offering pledge to building fund",
    )
    assert updated.contribution.amount == Decimal("400.00")
    assert updated.contribution.contribution_type == building_type


def test_m5_bt_007_manual_receipt_edit_audit_and_reason_enforcement(manual_contribution, receipt_creator):
    """
    M5-BT-007: Manual edits require mandatory non-empty reason and record field-level ReceiptEdit entries.
    """
    receipt = ReceiptService.generate_receipt(manual_contribution, generated_by_user=receipt_creator)

    # Blank reason is rejected
    with pytest.raises(ValidationError, match="specific reason is required"):
        ReceiptService.edit_manual_receipt(
            receipt=receipt,
            user=receipt_creator,
            changed_fields={"amount": Decimal("450.00")},
            reason="",
        )

    # Valid edit records ReceiptEdit
    ReceiptService.edit_manual_receipt(
        receipt=receipt,
        user=receipt_creator,
        changed_fields={"amount": Decimal("450.00"), "reference_number": "CHEQUE-999"},
        reason="Corrected cheque reference and added top-up",
    )

    edits = ReceiptEdit.objects.filter(receipt=receipt).order_by("field_name")
    assert edits.count() == 2
    amount_edit = edits.filter(field_name="amount").first()
    assert amount_edit.old_value == "350.00"
    assert amount_edit.new_value == "450.00"
    assert amount_edit.reason == "Corrected cheque reference and added top-up"

    # Audit log check
    audit = AuditLog.objects.filter(entity_type="Receipt", entity_id=receipt.id, action="RECEIPT_EDITED").first()
    assert audit is not None


def test_m5_bt_008_manual_receipt_soft_delete(manual_contribution, receipt_creator, other_system_user):
    """
    M5-BT-008: Manual receipts can be soft-deleted by their creator with receipt.delete_own.
    """
    receipt = ReceiptService.generate_receipt(manual_contribution, generated_by_user=receipt_creator)

    # Non-creator cannot delete
    with pytest.raises(PermissionDenied, match="Only the creator of a manual receipt may delete it"):
        ReceiptService.delete_manual_receipt(receipt, user=other_system_user, reason="Unauthorized delete")

    # Creator soft-deletes
    deleted = ReceiptService.delete_manual_receipt(receipt, user=receipt_creator, reason="Duplicate manual entry")
    assert deleted.status == ReceiptStatus.DELETED
    assert deleted.deleted_by == receipt_creator
    assert deleted.deleted_at is not None

    # Audit record check
    audit = AuditLog.objects.filter(entity_type="Receipt", entity_id=receipt.id, action="RECEIPT_DELETED").first()
    assert audit is not None


def test_m5_bt_009_physical_deletion_blocked(manual_contribution, receipt_creator):
    """
    M5-BT-009: Physical deletion of receipts is strictly prohibited on instance and queryset levels.
    """
    receipt = ReceiptService.generate_receipt(manual_contribution, generated_by_user=receipt_creator)

    # Instance delete blocked
    with pytest.raises(ValidationError, match="Receipt records cannot be physically deleted"):
        receipt.delete()

    # QuerySet bulk delete blocked
    with pytest.raises(ValidationError, match="Receipt records cannot be physically deleted"):
        Receipt.objects.filter(id=receipt.id).delete()


def test_m5_bt_010_soft_delete_querysets_and_admin_visibility(manual_contribution, receipt_creator, system_admin):
    """
    M5-BT-010: Active manager excludes soft-deleted receipts; all_objects includes them.
    """
    receipt = ReceiptService.generate_receipt(manual_contribution, generated_by_user=receipt_creator)
    receipt_num = receipt.receipt_number

    # Soft delete
    ReceiptService.delete_manual_receipt(receipt, user=receipt_creator, reason="Voided receipt")

    # Default/Active manager does not return it
    assert not Receipt.objects.filter(receipt_number=receipt_num).exists()

    # all_objects retains historical record
    historical = Receipt.all_objects.filter(receipt_number=receipt_num).first()
    assert historical is not None
    assert historical.status == ReceiptStatus.DELETED


def test_m5_bt_011_contributor_phone_number_privacy_invariant(manual_contribution, receipt_creator):
    """
    M5-BT-011: Contributor phone numbers must NEVER appear in receipt HTML, PDF, or verification output.
    """
    receipt = ReceiptService.generate_receipt(manual_contribution, generated_by_user=receipt_creator)
    phone = manual_contribution.member.phone_numbers.first().phone_number

    # 1. Check ReceiptPdfService rendered HTML/content
    pdf_bytes = ReceiptPdfService.render_pdf(receipt)
    assert phone.encode() not in pdf_bytes

    # 2. Check Verification result
    verify_result = ReceiptService.verify_receipt(receipt.receipt_number)
    result_str = str(verify_result)
    assert phone not in result_str

    # 3. Check contributor name property
    assert phone not in receipt.contributor_name


def test_m5_bt_012_pdf_generation_and_storage(manual_contribution, receipt_creator):
    """
    M5-BT-012: PDF is generated and persisted in StorageService with valid %PDF header.
    """
    receipt = ReceiptService.generate_receipt(manual_contribution, generated_by_user=receipt_creator)

    storage = get_storage_service()
    assert storage.exists(receipt.pdf_storage_key)

    pdf_bytes = storage.get(receipt.pdf_storage_key)
    assert pdf_bytes.startswith(b"%PDF")
    assert len(pdf_bytes) > 1000


def test_m5_bt_013_public_receipt_verification(manual_contribution, receipt_creator):
    """
    M5-BT-013: Receipt verification validates active, soft-deleted, and non-existent receipts.
    """
    receipt = ReceiptService.generate_receipt(manual_contribution, generated_by_user=receipt_creator)

    # 1. Active receipt
    v_active = ReceiptService.verify_receipt(receipt.receipt_number)
    assert v_active["is_valid"] is True
    assert v_active["status"] == "VALID"
    assert v_active["contributor_name"] == "Kwame Mensah"
    assert v_active["amount"] == Decimal("350.00")

    # 2. Soft-deleted receipt
    ReceiptService.delete_manual_receipt(receipt, user=receipt_creator, reason="Voided")
    v_deleted = ReceiptService.verify_receipt(receipt.receipt_number)
    assert v_deleted["is_valid"] is False
    assert v_deleted["status"] == "DELETED"
    assert "revoked" in v_deleted["message"].lower()

    # 3. Non-existent receipt
    v_none = ReceiptService.verify_receipt("HLC-0000000")
    assert v_none["is_valid"] is False
    assert v_none["status"] == "NOT_FOUND"


def test_m5_bt_014_secure_access_token_workflow(manual_contribution, receipt_creator):
    """
    M5-BT-014: Secure token allows tamper-proof access for external SMS downloads.
    """
    receipt = ReceiptService.generate_receipt(manual_contribution, generated_by_user=receipt_creator)

    token = ReceiptService.generate_secure_access_token(receipt)
    resolved = ReceiptService.get_receipt_by_access_token(token)
    assert resolved is not None
    assert resolved.id == receipt.id

    # Tampered token fails
    tampered = token + "xyz"
    assert ReceiptService.get_receipt_by_access_token(tampered) is None


def test_m5_bt_015_automatic_sms_dispatch_and_missing_phone_graceful_handling(auto_contribution):
    """
    M5-BT-015: Automatic receipt dispatches SMS notification; missing phone is handled gracefully without failing.
    """
    # Auto contribution with phone
    receipt = ReceiptService.generate_receipt(auto_contribution, is_system=True)
    delivery = ReceiptDeliveryRecord.objects.filter(receipt=receipt).first()
    assert delivery is not None
    assert delivery.status == "SENT"
    assert delivery.recipient_phone == "+233241112233"
    assert Notification.objects.filter(related_record_id=receipt.id, notification_type=NotificationType.RECEIPT).exists()

    # Auto contribution with missing phone should NOT crash
    tc_no_phone = TemporaryContributor.objects.create(
        phone_number="",
        provider="HUBTEL",
        provider_name="Unknown Donor",
    )
    txn2 = PaymentTransaction.objects.create(
        provider="HUBTEL",
        provider_reference="TXN-HUB-NO-PHONE",
        transaction_phone="",
        amount=Decimal("100.00"),
        currency="GHS",
        status=PaymentTransactionStatus.SUCCESSFUL,
    )
    c_no_phone = Contribution.objects.create(
        temporary_contributor=tc_no_phone,
        contribution_type=auto_contribution.contribution_type,
        amount=Decimal("100.00"),
        currency="GHS",
        payment_mode=PaymentMode.MOBILE_MONEY,
        entry_method=EntryMethod.AUTOMATIC,
        payment_transaction=txn2,
        status=ContributionStatus.CONFIRMED,
        contribution_date=datetime.date(2026, 9, 23),
    )
    receipt_no_phone = ReceiptService.generate_receipt(c_no_phone, is_system=True)
    delivery_fail = ReceiptDeliveryRecord.objects.filter(receipt=receipt_no_phone).first()
    assert delivery_fail is not None
    assert delivery_fail.status == "FAILED"
    assert "No valid recipient phone number" in delivery_fail.failure_reason


def test_m5_bt_016_receipt_ui_views_and_permissions(client, manual_contribution, receipt_creator, other_system_user, system_admin):
    """
    M5-BT-016: UI routes for list, detail, pdf export, edit, and public verify enforce permissions.
    """
    receipt = ReceiptService.generate_receipt(manual_contribution, generated_by_user=receipt_creator)

    # 1. Unauthenticated access redirects to login
    resp = client.get(reverse("receipts:receipt_list"))
    assert resp.status_code == 302
    assert "/login/" in resp.url

    # 2. Creator can view receipt list & detail
    client.force_login(receipt_creator)
    resp = client.get(reverse("receipts:receipt_list"))
    assert resp.status_code == 200
    assert receipt.receipt_number in resp.content.decode()

    resp = client.get(reverse("receipts:receipt_detail", kwargs={"receipt_id": receipt.id}))
    assert resp.status_code == 200
    assert "Kwame Mensah" in resp.content.decode()

    # 3. PDF streaming endpoint works
    resp = client.get(reverse("receipts:receipt_pdf", kwargs={"receipt_id": receipt.id}))
    assert resp.status_code == 200
    assert resp["Content-Type"] == "application/pdf"
    assert resp.content.startswith(b"%PDF")

    # 4. Public verification portal is accessible without login
    client.logout()
    resp = client.get(reverse("receipts:receipt_verify_number", kwargs={"receipt_number": receipt.receipt_number}))
    assert resp.status_code == 200
    assert "Official Receipt Verified: VALID" in resp.content.decode()
    assert "Kwame Mensah" in resp.content.decode()
