import pytest
from django.core.exceptions import ValidationError
from apps.members.models import (
    Member,
    MemberPhoneNumber,
    TemporaryContributor,
    MemberMergeRequest,
    MemberStatus,
    TemporaryContributorStatus,
)


def test_member_creation_and_id_generation(db):
    member = Member.objects.create(
        first_name="Kwame",
        last_name="Mensah",
        email="kwame.mensah@example.com",
        ministry="Youth Ministry"
    )
    assert member.member_number.startswith("MEM-")
    assert len(member.member_number) == 10  # MEM-000001
    assert member.full_name == "Kwame Mensah"
    assert member.status == MemberStatus.ACTIVE


def test_member_multiple_phones_and_primary(db):
    member = Member.objects.create(first_name="Ama", last_name="Osei")
    p1 = MemberPhoneNumber.objects.create(
        member=member,
        phone_number="+233240000001",
        is_primary=True,
        is_active=True
    )
    p2 = MemberPhoneNumber.objects.create(
        member=member,
        phone_number="+233200000002",
        is_primary=False,
        is_active=True
    )
    assert member.phone_numbers.count() == 2

    # Attempting to add another active primary phone should fail
    with pytest.raises(ValidationError, match="already has an active primary phone"):
        MemberPhoneNumber.objects.create(
            member=member,
            phone_number="+233550000003",
            is_primary=True,
            is_active=True
        )


def test_active_phone_conflict_across_members(db):
    """Enforce Business Principle 10: An active phone cannot silently belong to multiple members."""
    m1 = Member.objects.create(first_name="Kofi", last_name="Appiah")
    m2 = Member.objects.create(first_name="Abena", last_name="Appiah")

    MemberPhoneNumber.objects.create(
        member=m1,
        phone_number="+233244112233",
        is_active=True
    )

    with pytest.raises(ValidationError, match="already actively assigned to member"):
        MemberPhoneNumber.objects.create(
            member=m2,
            phone_number="+233244112233",
            is_active=True
        )


def test_temporary_contributor_creation(db):
    tc = TemporaryContributor.objects.create(
        phone_number="+233249999999",
        provider="MTN_MOMO",
        provider_name="UNKNOWN SENDER"
    )
    assert tc.status == TemporaryContributorStatus.UNVERIFIED


def test_member_merge_request_validation(system_admin):
    m1 = Member.objects.create(first_name="Yaw", last_name="Boateng")

    # Cannot merge same member
    with pytest.raises(ValidationError, match="Source member and target member cannot be identical"):
        MemberMergeRequest.objects.create(
            source_member=m1,
            target_member=m1,
            requested_by=system_admin,
            reason="Duplicate registration"
        )
