from decimal import Decimal
import re
import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from apps.contributions.models import Contribution, ContributionType, PaymentMode, EntryMethod
from apps.receipts.models import Receipt, ReceiptStatus
from apps.members.models import Member


@pytest.fixture
def sample_contribution(seed_data):
    member = Member.objects.create(first_name="Grace", last_name="Ansah")
    c_type = ContributionType.objects.get(name="Tithe")
    return Contribution.objects.create(
        member=member,
        contribution_type=c_type,
        amount=Decimal("300.00"),
        currency="GHS",
        payment_mode=PaymentMode.CASH,
        entry_method=EntryMethod.MANUAL
    )


def test_receipt_number_format_and_generation(sample_contribution, system_user):
    receipt = Receipt.objects.create(
        contribution=sample_contribution,
        generated_by_user=system_user,
        generated_by_system=False,
        pdf_storage_key="receipts/2026/09/sample.pdf"
    )
    assert re.match(r"^HLC-\d{7}$", receipt.receipt_number)
    assert receipt.status == ReceiptStatus.ACTIVE


def test_automatic_receipt_immutability(sample_contribution):
    """Enforce Business Principle 14: Automatic receipts are immutable."""
    receipt = Receipt.objects.create(
        contribution=sample_contribution,
        generated_by_system=True,
        pdf_storage_key="receipts/auto/sample.pdf"
    )

    receipt.pdf_storage_key = "receipts/tampered/sample.pdf"
    with pytest.raises(ValidationError, match="Automatic receipts are finalized and immutable"):
        receipt.save()


def test_manual_receipt_soft_delete(sample_contribution, system_user):
    """Enforce Business Principle 17 & 18: Deleted manual receipts are soft-deleted and remain auditable."""
    receipt = Receipt.objects.create(
        contribution=sample_contribution,
        generated_by_user=system_user,
        generated_by_system=False,
        pdf_storage_key="receipts/manual/sample.pdf"
    )
    receipt_num = receipt.receipt_number

    # Soft delete
    receipt.soft_delete(user=system_user)

    # Active queryset does not return soft-deleted receipt
    assert not Receipt.objects.filter(receipt_number=receipt_num).exists()

    # All objects manager still retains the historical record
    assert Receipt.all_objects.filter(receipt_number=receipt_num).exists()
    deleted_record = Receipt.all_objects.get(receipt_number=receipt_num)
    assert deleted_record.status == ReceiptStatus.DELETED
    assert deleted_record.deleted_by == system_user


def test_automatic_receipt_cannot_be_deleted(sample_contribution, system_user):
    receipt = Receipt.objects.create(
        contribution=sample_contribution,
        generated_by_system=True,
        pdf_storage_key="receipts/auto/sample.pdf"
    )
    with pytest.raises(ValidationError, match="Automatic receipts are immutable and cannot be deleted"):
        receipt.soft_delete(user=system_user)
