import decimal
import pytest
from django.core.exceptions import ValidationError, PermissionDenied
from django.urls import reverse

from apps.accounts.models import Role, Permission, RolePermission
from apps.audit.models import AuditLog
from apps.contributions.models import (
    Contribution,
    ContributionType,
    PaymentMode,
    EntryMethod,
    ContributionStatus,
    PaymentTransaction,
    PaymentTransactionStatus,
)
from apps.members.models import (
    Member,
    MemberPhoneNumber,
    TemporaryContributor,
    MemberMergeRequest,
    MemberStatus,
    TemporaryContributorStatus,
    MemberMergeStatus,
)
from apps.members.services import (
    MemberService,
    ContributorIdentificationService,
    TemporaryContributorService,
    MemberMergeService,
)


@pytest.fixture
def member_clerk(seed_data, system_user):
    """System user with member.create and member.edit permissions."""
    role = system_user.user_roles.first().role
    perm_create = Permission.objects.get(code="member.create")
    perm_edit = Permission.objects.get(code="member.edit")
    RolePermission.objects.create(role=role, permission=perm_create)
    RolePermission.objects.create(role=role, permission=perm_edit)
    return system_user


def test_m3_bt_001_member_creation_with_system_id_and_initial_phone(seed_data, system_admin):
    """M3-BT-001: Member creation generates system ID and assigns initial primary phone."""
    member = MemberService.create_member(
        creator=system_admin,
        first_name="Kwame",
        last_name="Mensah",
        middle_name="Kofi",
        email="kwame.mensah@tcihlc.org",
        ministry="Youth Ministry",
        team="Protocol",
        department="Finance",
        phone_number="+233240000001",
        phone_provider="MTN",
        is_primary_phone=True,
    )

    assert member.member_number.startswith("MEM-")
    assert len(member.member_number) == 10
    assert member.full_name == "Kwame Kofi Mensah"
    assert member.status == MemberStatus.ACTIVE
    assert member.email == "kwame.mensah@tcihlc.org"
    assert member.ministry == "Youth Ministry"

    # Verify initial phone
    primary_phone = member.phone_numbers.filter(is_primary=True, is_active=True).first()
    assert primary_phone is not None
    assert primary_phone.phone_number == "+233240000001"
    assert primary_phone.provider == "MTN"

    # Audit record verified
    assert AuditLog.objects.filter(action="MEMBER_CREATED", entity_id=member.id).exists()


def test_m3_bt_002_multiple_phone_numbers_and_single_active_primary(seed_data, system_admin):
    """M3-BT-002: Member supports multiple phones with exactly one active primary phone."""
    member = MemberService.create_member(
        creator=system_admin,
        first_name="Ama",
        last_name="Osei",
        phone_number="+233240000011",
        is_primary_phone=True,
    )

    # 1. Add secondary active phone
    p2 = MemberService.add_phone_number(
        actor=system_admin,
        member=member,
        phone_number="+233200000022",
        provider="Telecel",
        is_primary=False,
    )
    assert member.phone_numbers.count() == 2
    assert member.phone_numbers.filter(is_primary=True).count() == 1
    assert not p2.is_primary

    # 2. Switch primary phone
    MemberService.set_primary_phone(actor=system_admin, member=member, phone_id=p2.id)
    p2.refresh_from_db()
    p1 = member.phone_numbers.get(phone_number="+233240000011")
    assert p2.is_primary is True
    assert p1.is_primary is False
    assert member.phone_numbers.filter(is_primary=True, is_active=True).count() == 1


def test_m3_bt_003_active_phone_conflict_across_members(seed_data, system_admin):
    """M3-BT-003: An active phone number cannot belong to two members simultaneously."""
    shared_phone = "+233244998877"

    # Member 1 has the active phone
    MemberService.create_member(
        creator=system_admin,
        first_name="Kofi",
        last_name="Boateng",
        phone_number=shared_phone,
    )

    # Member 2 registration with identical active phone must raise ValidationError
    with pytest.raises(ValidationError, match="already actively assigned to member"):
        MemberService.create_member(
            creator=system_admin,
            first_name="Abena",
            last_name="Boateng",
            phone_number=shared_phone,
        )

    # Adding to existing member 2 also raises ValidationError
    member2 = MemberService.create_member(
        creator=system_admin,
        first_name="Abena",
        last_name="Boateng",
        phone_number="+233551112233",
    )
    with pytest.raises(ValidationError, match="already actively assigned to member"):
        MemberService.add_phone_number(
            actor=system_admin,
            member=member2,
            phone_number=shared_phone,
        )


def test_m3_bt_004_inactive_phone_available_for_reassignment(seed_data, system_admin):
    """M3-BT-004: Inactive phone does not block another member from using it."""
    reassigned_phone = "+233249000111"

    m1 = MemberService.create_member(
        creator=system_admin,
        first_name="First",
        last_name="Owner",
        phone_number=reassigned_phone,
    )
    p1 = m1.phone_numbers.first()

    # Deactivate phone on m1
    MemberService.toggle_phone_status(actor=system_admin, phone=p1, is_active=False)
    p1.refresh_from_db()
    assert p1.is_active is False

    # Now Member 2 can register with that phone without conflict
    m2 = MemberService.create_member(
        creator=system_admin,
        first_name="Second",
        last_name="Owner",
        phone_number=reassigned_phone,
    )
    assert m2.phone_numbers.filter(phone_number=reassigned_phone, is_active=True).exists()


def test_m3_bt_005_member_search_criteria(client, seed_data, system_admin):
    """M3-BT-005: Member directory search works across number, name, phone, and email."""
    client.force_login(system_admin)

    m = MemberService.create_member(
        creator=system_admin,
        first_name="Emmanuel",
        last_name="Agyemang",
        email="emmanuel.agyemang@tcihlc.org",
        phone_number="+233247778899",
    )

    # 1. Search by member number
    r1 = client.get(reverse("members:member_list"), {"q": m.member_number})
    assert m.full_name in r1.content.decode()

    # 2. Search by last name
    r2 = client.get(reverse("members:member_list"), {"q": "Agyemang"})
    assert m.member_number in r2.content.decode()

    # 3. Search by phone number
    r3 = client.get(reverse("members:member_list"), {"q": "247778899"})
    assert m.full_name in r3.content.decode()

    # 4. Search by email
    r4 = client.get(reverse("members:member_list"), {"q": "emmanuel.agyemang"})
    assert m.member_number in r4.content.decode()


def test_m3_bt_006_strict_contributor_identification_by_phone(seed_data, system_admin):
    """
    M3-BT-006: Contributor identification is strictly phone-based.
    Provider name, account similarity, or email must never override the phone number.
    """
    real_member = MemberService.create_member(
        creator=system_admin,
        first_name="Samuel",
        last_name="Dadzie",
        email="samuel.dadzie@tcihlc.org",
        phone_number="+233241234567",
    )

    # Scenario A: Matching phone, but provider name differs completely
    resolved_m, tc_a = ContributorIdentificationService.identify_contributor(
        phone_number="+233241234567",
        provider="MTN_MOMO",
        provider_name="COMPLETELY DIFFERENT SENDER NAME",
    )
    assert resolved_m == real_member
    assert tc_a is None

    # Scenario B: Unknown phone, but provider name is identical to existing member
    resolved_m2, tc_b = ContributorIdentificationService.identify_contributor(
        phone_number="+233209876543",  # Unregistered phone
        provider="MTN_MOMO",
        provider_name="Samuel Dadzie",  # Identical name
    )
    # Must NOT identify as Samuel Dadzie because phone does not match
    assert resolved_m2 is None
    assert tc_b is not None
    assert tc_b.phone_number == "+233209876543"
    assert tc_b.status == TemporaryContributorStatus.UNVERIFIED


def test_m3_bt_007_unknown_transaction_phone_creates_temporary_contributor(seed_data):
    """M3-BT-007: Unknown incoming transaction creates Temporary Contributor record."""
    member, tc = ContributorIdentificationService.identify_contributor(
        phone_number="+233551999888",
        provider="MTN_MOMO",
        provider_name="UNKNOWN GIVER",
        provider_account_reference="TX-998811",
    )

    assert member is None
    assert tc is not None
    assert tc.phone_number == "+233551999888"
    assert tc.provider == "MTN_MOMO"
    assert tc.status == TemporaryContributorStatus.UNVERIFIED
    assert TemporaryContributor.objects.filter(phone_number="+233551999888").exists()


def test_m3_bt_008_temporary_contributor_conversion_to_member(seed_data, system_admin):
    """M3-BT-008: Converting Temporary Contributor to member preserves contributions."""
    _, tc = ContributorIdentificationService.identify_contributor(
        phone_number="+233248889900",
        provider="MTN_MOMO",
        provider_name="Ebenezer Quaye",
    )

    c_type = ContributionType.objects.first()
    tx1 = PaymentTransaction.objects.create(
        provider="MTN_MOMO",
        provider_reference="MTN-REF-TEST-008",
        transaction_phone="+233248889900",
        amount=decimal.Decimal("250.00"),
        status=PaymentTransactionStatus.SUCCESSFUL,
    )
    c1 = Contribution.objects.create(
        temporary_contributor=tc,
        contribution_type=c_type,
        amount=decimal.Decimal("250.00"),
        payment_mode=PaymentMode.MOBILE_MONEY,
        entry_method=EntryMethod.AUTOMATIC,
        payment_transaction=tx1,
        status=ContributionStatus.CONFIRMED,
    )

    new_member = TemporaryContributorService.convert_to_member(
        actor=system_admin,
        temporary_contributor=tc,
        first_name="Ebenezer",
        last_name="Quaye",
        ministry="Men of Faith",
    )

    tc.refresh_from_db()
    c1.refresh_from_db()

    assert tc.status == TemporaryContributorStatus.CONVERTED
    assert tc.converted_member == new_member
    assert c1.member == new_member
    assert c1.temporary_contributor is None
    assert new_member.phone_numbers.filter(phone_number="+233248889900", is_active=True).exists()


def test_m3_bt_009_temporary_contributor_linking_to_member(seed_data, system_admin):
    """M3-BT-009: Linking Temporary Contributor to existing member reassigns contributions."""
    existing_member = MemberService.create_member(
        creator=system_admin,
        first_name="Grace",
        last_name="Appiah",
        phone_number="+233240001122",
    )

    _, tc = ContributorIdentificationService.identify_contributor(
        phone_number="+233553334455",
        provider="MTN_MOMO",
    )

    c_type = ContributionType.objects.first()
    tx2 = PaymentTransaction.objects.create(
        provider="MTN_MOMO",
        provider_reference="MTN-REF-TEST-009",
        transaction_phone="+233553334455",
        amount=decimal.Decimal("500.00"),
        status=PaymentTransactionStatus.SUCCESSFUL,
    )
    c = Contribution.objects.create(
        temporary_contributor=tc,
        contribution_type=c_type,
        amount=decimal.Decimal("500.00"),
        payment_mode=PaymentMode.MOBILE_MONEY,
        entry_method=EntryMethod.AUTOMATIC,
        payment_transaction=tx2,
        status=ContributionStatus.CONFIRMED,
    )

    TemporaryContributorService.link_to_member(
        actor=system_admin,
        temporary_contributor=tc,
        target_member=existing_member,
        add_phone=True,
    )

    tc.refresh_from_db()
    c.refresh_from_db()

    assert tc.status == TemporaryContributorStatus.LINKED
    assert tc.converted_member == existing_member
    assert c.member == existing_member
    assert c.temporary_contributor is None
    assert existing_member.phone_numbers.filter(phone_number="+233553334455").exists()


def test_m3_bt_010_member_merge_request_initiation(seed_data, system_admin, system_user):
    """M3-BT-010: Member merge request requires reason and cannot merge member to self."""
    # Assign merge request permission to system_user role
    role = system_user.user_roles.first().role
    perm = Permission.objects.get(code="member.merge.request")
    RolePermission.objects.create(role=role, permission=perm)

    m1 = MemberService.create_member(creator=system_admin, first_name="Source", last_name="User")
    m2 = MemberService.create_member(creator=system_admin, first_name="Target", last_name="User")

    # Cannot merge member to self
    with pytest.raises(ValidationError, match="identical"):
        MemberMergeService.request_merge(
            requester=system_user,
            source_member=m1,
            target_member=m1,
            reason="Test merge",
        )

    # Successful merge request
    req = MemberMergeService.request_merge(
        requester=system_user,
        source_member=m1,
        target_member=m2,
        reason="Duplicate profile created with misspelled name",
    )
    assert req.status == MemberMergeStatus.PENDING
    assert req.source_member == m1
    assert req.target_member == m2

    # Cannot initiate another pending merge on same source member
    with pytest.raises(ValidationError, match="pending merge request already exists"):
        MemberMergeService.request_merge(
            requester=system_user,
            source_member=m1,
            target_member=m2,
            reason="Second attempt",
        )


def test_m3_bt_011_system_administrator_merge_approval_execution(seed_data, system_admin):
    """M3-BT-011: Administrator approval atomically reassigns contributions and phones, and deactivates source."""
    source_m = MemberService.create_member(
        creator=system_admin,
        first_name="Duplicate",
        last_name="Member",
        phone_number="+233249111222",
    )
    target_m = MemberService.create_member(
        creator=system_admin,
        first_name="Primary",
        last_name="Member",
        phone_number="+233208333444",
    )

    c_type = ContributionType.objects.first()
    Contribution.objects.create(
        member=source_m,
        contribution_type=c_type,
        amount=decimal.Decimal("150.00"),
        payment_mode=PaymentMode.CASH,
        entry_method=EntryMethod.MANUAL,
        status=ContributionStatus.CONFIRMED,
    )
    Contribution.objects.create(
        member=target_m,
        contribution_type=c_type,
        amount=decimal.Decimal("300.00"),
        payment_mode=PaymentMode.CASH,
        entry_method=EntryMethod.MANUAL,
        status=ContributionStatus.CONFIRMED,
    )

    req = MemberMergeService.request_merge(
        requester=system_admin,
        source_member=source_m,
        target_member=target_m,
        reason="Consolidating duplicate records",
    )

    # Approve merge
    MemberMergeService.review_merge(
        reviewer=system_admin,
        merge_request=req,
        approve=True,
        remarks="Verified identical person and approved",
    )

    req.refresh_from_db()
    source_m.refresh_from_db()

    assert req.status == MemberMergeStatus.APPROVED
    assert req.reviewed_by == system_admin
    assert source_m.status == MemberStatus.DEACTIVATED

    # Target member now has all 2 contributions (total GHS 450)
    assert Contribution.objects.filter(member=target_m).count() == 2
    assert Contribution.objects.filter(member=source_m).count() == 0

    # Source phone transferred to target as secondary active phone
    assert target_m.phone_numbers.filter(phone_number="+233249111222", is_active=True).exists()
    assert target_m.phone_numbers.get(phone_number="+233208333444").is_primary is True

    # Audit records emitted
    assert AuditLog.objects.filter(action="MEMBER_MERGE_APPROVED", entity_id=req.id).exists()
    assert AuditLog.objects.filter(action="MEMBER_DEACTIVATED", entity_id=source_m.id).exists()


def test_m3_bt_012_non_administrator_cannot_approve_merge(seed_data, system_admin, system_user):
    """M3-BT-012: Non-administrators cannot approve member merge requests."""
    source_m = MemberService.create_member(creator=system_admin, first_name="A", last_name="One")
    target_m = MemberService.create_member(creator=system_admin, first_name="B", last_name="Two")
    req = MemberMergeService.request_merge(requester=system_admin, source_member=source_m, target_member=target_m, reason="Merge")

    with pytest.raises(ValidationError, match="Only the System Administrator"):
        MemberMergeService.review_merge(
            reviewer=system_user,
            merge_request=req,
            approve=True,
        )


def test_m3_bt_013_member_deactivation_preserves_historical_records(seed_data, system_admin):
    """M3-BT-013: Member deactivation preserves historical records without cascading deletes."""
    member = MemberService.create_member(
        creator=system_admin,
        first_name="Past",
        last_name="Member",
        phone_number="+233240009988",
    )
    c_type = ContributionType.objects.first()
    contrib = Contribution.objects.create(
        member=member,
        contribution_type=c_type,
        amount=decimal.Decimal("100.00"),
        payment_mode=PaymentMode.CASH,
        entry_method=EntryMethod.MANUAL,
        status=ContributionStatus.CONFIRMED,
    )

    MemberService.deactivate_member(
        actor=system_admin,
        member=member,
        reason="Relocated abroad",
    )

    member.refresh_from_db()
    assert member.status == MemberStatus.DEACTIVATED

    # Historical contributions and phones remain intact
    assert Contribution.objects.filter(id=contrib.id, member=member).exists()
    assert member.phone_numbers.count() >= 1
    assert AuditLog.objects.filter(action="MEMBER_DEACTIVATED", entity_id=member.id).exists()


def test_m3_bt_014_member_management_ui_permission_boundaries(client, seed_data, system_user, system_admin):
    """M3-BT-014: UI permission boundaries block unauthorized users from member mutation."""
    client.force_login(system_user)

    # 1. Without member.create, member_create is blocked with 403
    resp_create = client.get(reverse("members:member_create"))
    assert resp_create.status_code == 403

    m = MemberService.create_member(creator=system_admin, first_name="Test", last_name="User")

    # 2. Without member.edit, member_edit is blocked with 403
    resp_edit = client.get(reverse("members:member_edit", kwargs={"member_id": m.id}))
    assert resp_edit.status_code == 403

    # 3. Non-administrator cannot review merge
    m2 = MemberService.create_member(creator=system_admin, first_name="Target", last_name="User")
    merge_req = MemberMergeService.request_merge(requester=system_admin, source_member=m, target_member=m2, reason="Test")
    resp_merge = client.post(reverse("members:merge_review", kwargs={"request_id": merge_req.id}), {
        "decision": "approve",
    })
    assert resp_merge.status_code == 403
