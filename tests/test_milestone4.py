import datetime
from decimal import Decimal
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
from apps.members.models import Member, MemberPhoneNumber, TemporaryContributor, MemberStatus


@pytest.fixture
def tithe_type(seed_data):
    return ContributionType.objects.get(name="Tithe")


@pytest.fixture
def thanksgiving_type(seed_data):
    return ContributionType.objects.get(name="Thanksgiving")


@pytest.fixture
def partners_type(seed_data):
    return ContributionType.objects.get(name="Higher Life Partners")


@pytest.fixture
def building_type(seed_data):
    return ContributionType.objects.get(name="Building Project")


@pytest.fixture
def other_type(seed_data):
    return ContributionType.objects.get(name="Other")


@pytest.fixture
def active_member(db):
    member = Member.objects.create(
        first_name="Kofi",
        last_name="Mensah",
        email="kofi.mensah@tcihlc.org",
        status=MemberStatus.ACTIVE,
    )
    MemberPhoneNumber.objects.create(
        member=member,
        phone_number="+233240001122",
        provider="MTN",
        is_primary=True,
        is_active=True,
    )
    return member


@pytest.fixture
def deactivated_member(db):
    return Member.objects.create(
        first_name="Kwesi",
        last_name="Appiah",
        status=MemberStatus.DEACTIVATED,
    )


@pytest.fixture
def contribution_officer(seed_data):
    """User with contribution.create and contribution.view permissions."""
    role = Role.objects.create(name="Contribution Officer", description="Records and views contributions")
    perm_create = Permission.objects.get(code="contribution.create")
    perm_view = Permission.objects.get(code="contribution.view")
    RolePermission.objects.create(role=role, permission=perm_create)
    RolePermission.objects.create(role=role, permission=perm_view)

    user = User.objects.create_user(
        email="officer@tcihlc.org",
        password="ValidPassword123!",
        first_name="Finance",
        last_name="Officer",
    )
    UserRole.objects.create(user=user, role=role)
    return user


@pytest.fixture
def unauthorized_user(seed_data):
    """User with no contribution permissions."""
    role = Role.objects.create(name="Restricted Role", description="No contribution access")
    user = User.objects.create_user(
        email="restricted@tcihlc.org",
        password="ValidPassword123!",
        first_name="Restricted",
        last_name="User",
    )
    UserRole.objects.create(user=user, role=role)
    return user


# =========================================================================
# TEST SUITE: MILESTONE 4 — CONTRIBUTION MANAGEMENT
# =========================================================================


def test_m4_bt_001_manual_contribution_creation_and_id_format(active_member, tithe_type, contribution_officer):
    """
    M4-BT-001: Manual contribution creation generates unique CON-XXXXXXXX ID,
    stores contribution date, preserves created_at timestamp, and emits audit trail.
    """
    test_date = datetime.date(2026, 9, 15)
    contribution = ContributionService.record_manual_contribution(
        recorder=contribution_officer,
        member=active_member,
        contribution_type=tithe_type,
        amount=Decimal("350.00"),
        payment_mode=PaymentMode.CASH,
        contribution_date=test_date,
    )

    # 1. Contribution ID format verification (CON-00001245: CON- + 8 digits = 12 chars)
    assert contribution.contribution_number.startswith("CON-")
    assert len(contribution.contribution_number) == 12
    assert contribution.amount == Decimal("350.00")
    assert contribution.currency == "GHS"
    assert contribution.payment_mode == PaymentMode.CASH
    assert contribution.entry_method == EntryMethod.MANUAL
    assert contribution.status == ContributionStatus.CONFIRMED
    assert contribution.member == active_member
    assert contribution.temporary_contributor is None
    assert contribution.payment_transaction is None

    # 2. Date preservation (Historical contribution date vs record created_at)
    assert contribution.contribution_date == test_date
    assert contribution.created_at is not None
    assert contribution.created_at.date() == timezone.now().date()

    # 3. Audit trail verification
    audit_entry = AuditLog.objects.filter(
        action="CONTRIBUTION_CREATED",
        entity_id=contribution.id,
    ).first()
    assert audit_entry is not None
    assert audit_entry.user == contribution_officer
    assert audit_entry.new_values["contribution_number"] == contribution.contribution_number
    assert audit_entry.new_values["amount"] == "350.00"
    assert audit_entry.new_values["contribution_date"] == "2026-09-15"


def test_m4_bt_002_all_five_contribution_types_supported(
    active_member,
    tithe_type,
    thanksgiving_type,
    partners_type,
    building_type,
    other_type,
    contribution_officer,
):
    """
    M4-BT-002: All 5 approved contribution types are supported.
    'Other' requires a custom description, while standard types do not.
    """
    # 1. Tithe
    c1 = ContributionService.record_manual_contribution(
        recorder=contribution_officer,
        member=active_member,
        contribution_type=tithe_type,
        amount=Decimal("100.00"),
        payment_mode=PaymentMode.CASH,
    )
    assert c1.contribution_type.name == "Tithe"

    # 2. Thanksgiving
    c2 = ContributionService.record_manual_contribution(
        recorder=contribution_officer,
        member=active_member,
        contribution_type=thanksgiving_type,
        amount=Decimal("150.00"),
        payment_mode=PaymentMode.CASH,
    )
    assert c2.contribution_type.name == "Thanksgiving"

    # 3. Higher Life Partners
    c3 = ContributionService.record_manual_contribution(
        recorder=contribution_officer,
        member=active_member,
        contribution_type=partners_type,
        amount=Decimal("200.00"),
        payment_mode=PaymentMode.CASH,
    )
    assert c3.contribution_type.name == "Higher Life Partners"

    # 4. Building Project
    c4 = ContributionService.record_manual_contribution(
        recorder=contribution_officer,
        member=active_member,
        contribution_type=building_type,
        amount=Decimal("500.00"),
        payment_mode=PaymentMode.CASH,
    )
    assert c4.contribution_type.name == "Building Project"

    # 5. Other with description -> Accepted
    c5 = ContributionService.record_manual_contribution(
        recorder=contribution_officer,
        member=active_member,
        contribution_type=other_type,
        amount=Decimal("75.00"),
        payment_mode=PaymentMode.CASH,
        custom_type_description="Youth Ministry Support",
    )
    assert c5.contribution_type.name == "Other"
    assert c5.custom_type_description == "Youth Ministry Support"

    # 6. Other without description -> Rejected
    with pytest.raises(ValidationError, match="Description is required"):
        ContributionService.record_manual_contribution(
            recorder=contribution_officer,
            member=active_member,
            contribution_type=other_type,
            amount=Decimal("75.00"),
            payment_mode=PaymentMode.CASH,
            custom_type_description="",
        )


def test_m4_bt_003_all_payment_modes_and_bank_transaction_display(active_member, tithe_type, contribution_officer):
    """
    M4-BT-003: All 5 approved payment modes are accepted and Bank Transaction is explicitly displayed.
    """
    modes = [
        (PaymentMode.CASH, "Cash", None),
        (PaymentMode.MOBILE_MONEY, "Mobile Money", "MM-2026-001"),
        (PaymentMode.CHEQUE, "Cheque", "CHQ-884920"),
        (PaymentMode.BANK_TRANSACTION, "Bank Transaction", "BT-990142"),
        (PaymentMode.OTHER, "Other", "REF-MISC-01"),
    ]

    for mode_val, expected_display, ref in modes:
        c = ContributionService.record_manual_contribution(
            recorder=contribution_officer,
            member=active_member,
            contribution_type=tithe_type,
            amount=Decimal("50.00"),
            payment_mode=mode_val,
            reference_number=ref,
        )
        assert c.payment_mode == mode_val
        assert c.get_payment_mode_display() == expected_display
        if ref:
            assert c.reference_number == ref


def test_m4_bt_004_amount_validation_enforced(active_member, tithe_type, contribution_officer):
    """
    M4-BT-004: Amount validation rejects zero, negative, invalid, or empty amounts.
    """
    # 1. Zero amount rejected
    with pytest.raises(ValidationError, match="greater than zero"):
        ContributionService.record_manual_contribution(
            recorder=contribution_officer,
            member=active_member,
            contribution_type=tithe_type,
            amount=Decimal("0.00"),
            payment_mode=PaymentMode.CASH,
        )

    # 2. Negative amount rejected
    with pytest.raises(ValidationError, match="greater than zero"):
        ContributionService.record_manual_contribution(
            recorder=contribution_officer,
            member=active_member,
            contribution_type=tithe_type,
            amount=Decimal("-50.00"),
            payment_mode=PaymentMode.CASH,
        )

    # 3. Non-numeric string rejected
    with pytest.raises(ValidationError, match="valid numeric"):
        ContributionService.record_manual_contribution(
            recorder=contribution_officer,
            member=active_member,
            contribution_type=tithe_type,
            amount="abc",
            payment_mode=PaymentMode.CASH,
        )

    # 4. Empty amount rejected
    with pytest.raises(ValidationError, match="required"):
        ContributionService.record_manual_contribution(
            recorder=contribution_officer,
            member=active_member,
            contribution_type=tithe_type,
            amount="",
            payment_mode=PaymentMode.CASH,
        )


def test_m4_bt_005_member_association_rules(active_member, deactivated_member, tithe_type, contribution_officer):
    """
    M4-BT-005: Member association rules: valid member accepted, deactivated member rejected,
    and relationship preserved.
    """
    # 1. Active member accepted
    c = ContributionService.record_manual_contribution(
        recorder=contribution_officer,
        member=active_member,
        contribution_type=tithe_type,
        amount=Decimal("120.00"),
        payment_mode=PaymentMode.CASH,
    )
    assert c.member == active_member
    assert active_member.contributions.filter(id=c.id).exists()

    # 2. Deactivated member rejected
    with pytest.raises(ValidationError, match="deactivated member"):
        ContributionService.record_manual_contribution(
            recorder=contribution_officer,
            member=deactivated_member,
            contribution_type=tithe_type,
            amount=Decimal("100.00"),
            payment_mode=PaymentMode.CASH,
        )

    # 3. Missing member rejected
    with pytest.raises(ValidationError, match="valid member must be selected"):
        ContributionService.record_manual_contribution(
            recorder=contribution_officer,
            member=None,
            contribution_type=tithe_type,
            amount=Decimal("100.00"),
            payment_mode=PaymentMode.CASH,
        )


def test_m4_bt_006_permissions_enforced_for_creation(
    active_member, tithe_type, system_admin, contribution_officer, unauthorized_user
):
    """
    M4-BT-006: Backend permission check: Administrator & authorized user can create,
    unauthorized user is blocked.
    """
    # 1. System Administrator can record
    c_admin = ContributionService.record_manual_contribution(
        recorder=system_admin,
        member=active_member,
        contribution_type=tithe_type,
        amount=Decimal("200.00"),
        payment_mode=PaymentMode.CASH,
    )
    assert c_admin.recorded_by == system_admin

    # 2. User with contribution.create can record
    c_user = ContributionService.record_manual_contribution(
        recorder=contribution_officer,
        member=active_member,
        contribution_type=tithe_type,
        amount=Decimal("200.00"),
        payment_mode=PaymentMode.CASH,
    )
    assert c_user.recorded_by == contribution_officer

    # 3. Unauthorized user is blocked
    with pytest.raises(PermissionDenied):
        ContributionService.record_manual_contribution(
            recorder=unauthorized_user,
            member=active_member,
            contribution_type=tithe_type,
            amount=Decimal("200.00"),
            payment_mode=PaymentMode.CASH,
        )


def test_m4_bt_007_permissions_enforced_for_views(client, active_member, tithe_type, contribution_officer, unauthorized_user):
    """
    M4-BT-007: Authorized users can view contributions, unauthorized users receive 403 Forbidden.
    """
    c = ContributionService.record_manual_contribution(
        recorder=contribution_officer,
        member=active_member,
        contribution_type=tithe_type,
        amount=Decimal("100.00"),
        payment_mode=PaymentMode.CASH,
    )

    # 1. Authorized user -> 200 OK
    client.force_login(contribution_officer)
    res_list = client.get(reverse("contributions:contribution_list"))
    assert res_list.status_code == 200
    assert c.contribution_number in res_list.content.decode()

    res_detail = client.get(reverse("contributions:contribution_detail", args=[c.id]))
    assert res_detail.status_code == 200
    assert "Bank Transaction" in res_detail.content.decode() or "Cash" in res_detail.content.decode()

    # 2. Unauthorized user -> 403 Forbidden
    client.force_login(unauthorized_user)
    res_unauth_list = client.get(reverse("contributions:contribution_list"))
    assert res_unauth_list.status_code == 403

    res_unauth_detail = client.get(reverse("contributions:contribution_detail", args=[c.id]))
    assert res_unauth_detail.status_code == 403


def test_m4_bt_008_financial_record_integrity_prevents_physical_deletion(active_member, tithe_type, contribution_officer):
    """
    M4-BT-008: Financial records cannot be physically deleted.
    Both instance .delete() and queryset .delete() raise ValidationError.
    """
    c = ContributionService.record_manual_contribution(
        recorder=contribution_officer,
        member=active_member,
        contribution_type=tithe_type,
        amount=Decimal("400.00"),
        payment_mode=PaymentMode.CASH,
    )

    # 1. Instance delete raises ValidationError
    with pytest.raises(ValidationError, match="cannot be physically deleted"):
        c.delete()

    assert Contribution.objects.filter(id=c.id).exists()

    # 2. QuerySet bulk delete raises ValidationError
    with pytest.raises(ValidationError, match="cannot be physically deleted"):
        Contribution.objects.filter(id=c.id).delete()

    assert Contribution.objects.filter(id=c.id).exists()


def test_m4_bt_009_audit_trail_recorded_and_immutable(active_member, tithe_type, contribution_officer):
    """
    M4-BT-009: Contribution actions generate immutable audit log records.
    """
    c = ContributionService.record_manual_contribution(
        recorder=contribution_officer,
        member=active_member,
        contribution_type=tithe_type,
        amount=Decimal("600.00"),
        payment_mode=PaymentMode.BANK_TRANSACTION,
        reference_number="BT-2026-777",
    )

    log = AuditLog.objects.filter(action="CONTRIBUTION_CREATED", entity_id=c.id).first()
    assert log is not None
    assert log.user == contribution_officer
    assert log.new_values["contribution_number"] == c.contribution_number
    assert log.new_values["amount"] == "600.00"
    assert log.new_values["payment_mode"] == "BANK_TRANSACTION"
    assert log.new_values["reference_number"] == "BT-2026-777"

    # Audit records cannot be modified
    with pytest.raises(PermissionDenied, match="Audit records are immutable"):
        log.reason = "Altered reason"
        log.save()


def test_m4_bt_010_search_and_filter_contributions(active_member, tithe_type, thanksgiving_type, contribution_officer):
    """
    M4-BT-010: Search and filter contributions by ID, member, type, mode, entry method, and date range.
    """
    # Contribution A: Tithe, Cash, 2026-09-01
    cA = ContributionService.record_manual_contribution(
        recorder=contribution_officer,
        member=active_member,
        contribution_type=tithe_type,
        amount=Decimal("100.00"),
        payment_mode=PaymentMode.CASH,
        contribution_date="2026-09-01",
    )

    # Contribution B: Thanksgiving, Bank Transaction, 2026-09-20, Ref BT-999
    cB = ContributionService.record_manual_contribution(
        recorder=contribution_officer,
        member=active_member,
        contribution_type=thanksgiving_type,
        amount=Decimal("250.00"),
        payment_mode=PaymentMode.BANK_TRANSACTION,
        reference_number="BT-999",
        contribution_date="2026-09-20",
    )

    # 1. Search by Contribution ID
    res = ContributionService.search_contributions(query=cA.contribution_number)
    assert cA in res
    assert cB not in res

    # 2. Search by Member name
    res = ContributionService.search_contributions(query=active_member.last_name)
    assert cA in res
    assert cB in res

    # 3. Search by Reference Number
    res = ContributionService.search_contributions(query="BT-999")
    assert cB in res
    assert cA not in res

    # 4. Filter by Contribution Type
    res = ContributionService.search_contributions(contribution_type_id=str(thanksgiving_type.id))
    assert cB in res
    assert cA not in res

    # 5. Filter by Payment Mode
    res = ContributionService.search_contributions(payment_mode=PaymentMode.BANK_TRANSACTION)
    assert cB in res
    assert cA not in res

    # 6. Filter by Date Range
    res = ContributionService.search_contributions(date_from="2026-09-15", date_to="2026-09-25")
    assert cB in res
    assert cA not in res


def test_m4_bt_011_automatic_contribution_foundation_and_phone_resolution(active_member, tithe_type):
    """
    M4-BT-011: Automatic contribution requires payment transaction and resolves contributor
    strictly by phone number (M3 rule).
    """
    # 1. Matching Phone Number -> Resolves to active Member
    txn_match = PaymentTransaction.objects.create(
        provider="MTN_MOMO",
        provider_reference="TXN-MOMO-001",
        transaction_phone="+233240001122",  # Matches active_member
        amount=Decimal("50.00"),
        status=PaymentTransactionStatus.SUCCESSFUL,
        confirmed_at=timezone.now(),
    )
    c_auto_member = ContributionService.record_automatic_contribution(
        payment_transaction=txn_match,
        contribution_type=tithe_type,
    )
    assert c_auto_member.member == active_member
    assert c_auto_member.temporary_contributor is None
    assert c_auto_member.entry_method == EntryMethod.AUTOMATIC
    assert c_auto_member.payment_transaction == txn_match

    # 2. Unknown Phone Number -> Resolves to Temporary Contributor (M3 rule)
    txn_unknown = PaymentTransaction.objects.create(
        provider="MTN_MOMO",
        provider_reference="TXN-MOMO-002",
        transaction_phone="+233249999999",  # Unregistered phone
        amount=Decimal("75.00"),
        status=PaymentTransactionStatus.SUCCESSFUL,
        confirmed_at=timezone.now(),
    )
    c_auto_temp = ContributionService.record_automatic_contribution(
        payment_transaction=txn_unknown,
        contribution_type=tithe_type,
    )
    assert c_auto_temp.member is None
    assert c_auto_temp.temporary_contributor is not None
    assert c_auto_temp.temporary_contributor.phone_number == "+233249999999"


def test_m4_bt_012_duplicate_protection_exact_duplicate_rule(active_member, tithe_type):
    """
    M4-BT-012: Duplicate Rule: Provider Reference Number + Contribution Type.
    Exact duplicate is blocked from creating a second contribution and marked DUPLICATE.
    """
    txn1 = PaymentTransaction.objects.create(
        provider="MTN_MOMO",
        provider_reference="TXN-DUP-001",
        transaction_phone="+233240001122",
        amount=Decimal("100.00"),
        status=PaymentTransactionStatus.SUCCESSFUL,
    )
    c1 = ContributionService.record_automatic_contribution(
        payment_transaction=txn1,
        contribution_type=tithe_type,
    )
    assert c1.status == ContributionStatus.CONFIRMED

    # Exact duplicate attempt: Same provider reference + Same contribution type
    txn2 = PaymentTransaction.objects.create(
        provider="MTN_MOMO",
        provider_reference="TXN-DUP-001",
        transaction_phone="+233240001122",
        amount=Decimal("100.00"),
        status=PaymentTransactionStatus.PENDING,
    )

    with pytest.raises(ValidationError, match="Duplicate contribution detected"):
        ContributionService.record_automatic_contribution(
            payment_transaction=txn2,
            contribution_type=tithe_type,
        )

    # Verify no second contribution was created
    assert Contribution.objects.filter(payment_transaction=txn2).count() == 0

    # Verify transaction status was updated to DUPLICATE
    txn2.refresh_from_db()
    assert txn2.status == PaymentTransactionStatus.DUPLICATE
    assert "Duplicate transaction" in txn2.failure_reason

    # Verify duplicate event audit trail
    assert AuditLog.objects.filter(action="DUPLICATE_CONTRIBUTION_BLOCKED", entity_id=txn2.id).exists()


def test_m4_bt_013_duplicate_protection_different_type_flagged_for_review(
    active_member, tithe_type, thanksgiving_type
):
    """
    M4-BT-013: When same provider reference appears with a DIFFERENT contribution type,
    it must be flagged for review rather than discarded.
    """
    txn1 = PaymentTransaction.objects.create(
        provider="MTN_MOMO",
        provider_reference="TXN-FLAG-001",
        transaction_phone="+233240001122",
        amount=Decimal("100.00"),
        status=PaymentTransactionStatus.SUCCESSFUL,
    )
    c1 = ContributionService.record_automatic_contribution(
        payment_transaction=txn1,
        contribution_type=tithe_type,
    )
    assert c1.status == ContributionStatus.CONFIRMED

    # Same provider reference with DIFFERENT contribution type (Thanksgiving)
    txn2 = PaymentTransaction.objects.create(
        provider="MTN_MOMO",
        provider_reference="TXN-FLAG-001",
        transaction_phone="+233240001122",
        amount=Decimal("100.00"),
        status=PaymentTransactionStatus.SUCCESSFUL,
    )

    c2 = ContributionService.record_automatic_contribution(
        payment_transaction=txn2,
        contribution_type=thanksgiving_type,
    )

    # Flagged for review
    assert c2.status == ContributionStatus.FLAGGED
    assert AuditLog.objects.filter(action="CONTRIBUTION_FLAGGED_FOR_REVIEW").exists()


def test_m4_bt_014_ui_contribution_create_workflow(client, active_member, tithe_type, other_type, contribution_officer):
    """
    M4-BT-014: UI form creation flow: GET loads form, valid POST creates contribution,
    invalid POST returns 400 with validation message.
    """
    client.force_login(contribution_officer)

    # 1. GET /contributions/create/
    res_get = client.get(reverse("contributions:contribution_create"))
    assert res_get.status_code == 200
    assert "Bank Transaction" in res_get.content.decode()
    assert "Record Church Contribution" in res_get.content.decode()

    # 2. POST valid manual contribution
    post_data = {
        "member_id": str(active_member.id),
        "contribution_type_id": str(tithe_type.id),
        "amount": "250.00",
        "payment_mode": PaymentMode.BANK_TRANSACTION,
        "reference_number": "WIRE-2026-441",
        "contribution_date": "2026-09-25",
    }
    res_post = client.post(reverse("contributions:contribution_create"), post_data)
    assert res_post.status_code == 302
    created_contrib = Contribution.objects.get(reference_number="WIRE-2026-441")
    assert created_contrib.amount == Decimal("250.00")
    assert created_contrib.contribution_date == datetime.date(2026, 9, 25)

    # 3. POST invalid amount -> 400
    invalid_post_data = post_data.copy()
    invalid_post_data["amount"] = "-10.00"
    res_invalid = client.post(reverse("contributions:contribution_create"), invalid_post_data)
    assert res_invalid.status_code == 400
    assert "greater than zero" in res_invalid.content.decode()

    # 4. POST 'Other' without description -> 400
    other_post_data = post_data.copy()
    other_post_data["contribution_type_id"] = str(other_type.id)
    other_post_data["custom_type_description"] = ""
    res_other_missing = client.post(reverse("contributions:contribution_create"), other_post_data)
    assert res_other_missing.status_code == 400
    assert "Description is required" in res_other_missing.content.decode()


def test_m4_bt_015_member_search_api(client, active_member, contribution_officer):
    """
    M4-BT-015: Live member search endpoint returns matching active members for contribution form.
    """
    client.force_login(contribution_officer)

    # Search with partial match
    res = client.get(reverse("contributions:member_search") + f"?q={active_member.first_name}")
    assert res.status_code == 200
    data = res.json()
    assert len(data["results"]) >= 1
    assert data["results"][0]["id"] == str(active_member.id)
    assert data["results"][0]["member_number"] == active_member.member_number
    assert data["results"][0]["phone"] == "+233240001122"
