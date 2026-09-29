from decimal import Decimal
import pytest
from django.core.exceptions import ValidationError
from apps.contributions.models import (
    Contribution,
    ContributionType,
    PaymentTransaction,
    PaymentMode,
    EntryMethod,
    ContributionStatus,
    PaymentTransactionStatus,
)
from apps.members.models import Member, TemporaryContributor


@pytest.fixture
def tithe_type(seed_data):
    return ContributionType.objects.get(name="Tithe")


@pytest.fixture
def sample_member(db):
    return Member.objects.create(first_name="Emmanuel", last_name="Quaye")


@pytest.fixture
def sample_temporary_contributor(db):
    return TemporaryContributor.objects.create(phone_number="+233240123456")


def test_manual_contribution_creation(sample_member, tithe_type, system_user):
    contribution = Contribution.objects.create(
        member=sample_member,
        contribution_type=tithe_type,
        amount=Decimal("250.00"),
        currency="GHS",
        payment_mode=PaymentMode.CASH,
        entry_method=EntryMethod.MANUAL,
        recorded_by=system_user
    )
    assert contribution.contribution_number.startswith("CON-")
    assert len(contribution.contribution_number) == 12  # CON-00000001
    assert contribution.amount == Decimal("250.00")
    assert contribution.status == ContributionStatus.CONFIRMED


def test_automatic_contribution_with_transaction(sample_member, tithe_type):
    txn = PaymentTransaction.objects.create(
        provider="MTN_MOMO",
        provider_reference="TXN-2026-999",
        transaction_phone="+233240000000",
        amount=Decimal("500.00"),
        currency="GHS",
        status=PaymentTransactionStatus.SUCCESSFUL
    )
    contribution = Contribution.objects.create(
        member=sample_member,
        payment_transaction=txn,
        contribution_type=tithe_type,
        amount=Decimal("500.00"),
        currency="GHS",
        payment_mode=PaymentMode.MOBILE_MONEY,
        entry_method=EntryMethod.AUTOMATIC
    )
    assert contribution.payment_transaction == txn


def test_automatic_contribution_requires_transaction(sample_member, tithe_type):
    """Automatic contributions must have an associated payment transaction."""
    with pytest.raises(ValidationError, match="Automatic contributions require an associated payment transaction"):
        Contribution.objects.create(
            member=sample_member,
            payment_transaction=None,
            contribution_type=tithe_type,
            amount=Decimal("100.00"),
            currency="GHS",
            payment_mode=PaymentMode.MOBILE_MONEY,
            entry_method=EntryMethod.AUTOMATIC
        )


def test_contributor_identity_xor(sample_member, sample_temporary_contributor, tithe_type):
    """A contribution must reference either a Member or TemporaryContributor, never both or neither."""
    # Neither
    with pytest.raises(ValidationError, match="exactly one contributor identity"):
        Contribution.objects.create(
            member=None,
            temporary_contributor=None,
            contribution_type=tithe_type,
            amount=Decimal("100.00"),
            currency="GHS",
            payment_mode=PaymentMode.CASH,
            entry_method=EntryMethod.MANUAL
        )

    # Both
    with pytest.raises(ValidationError, match="exactly one contributor identity"):
        Contribution.objects.create(
            member=sample_member,
            temporary_contributor=sample_temporary_contributor,
            contribution_type=tithe_type,
            amount=Decimal("100.00"),
            currency="GHS",
            payment_mode=PaymentMode.CASH,
            entry_method=EntryMethod.MANUAL
        )


def test_contribution_amount_must_be_positive(sample_member, tithe_type):
    with pytest.raises(ValidationError, match="greater than zero"):
        Contribution.objects.create(
            member=sample_member,
            contribution_type=tithe_type,
            amount=Decimal("0.00"),
            currency="GHS",
            payment_mode=PaymentMode.CASH,
            entry_method=EntryMethod.MANUAL
        )
