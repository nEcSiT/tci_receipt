from decimal import Decimal
import pytest
from django.core.exceptions import ValidationError
from apps.requisitions.models import (
    Requisition,
    RequisitionStatus,
    RequisitionApproval,
    RequisitionDisbursement,
    RequisitionAssignment,
    ApprovalDecision,
    DisbursementMethod,
)


def test_requisition_creation(db):
    req = Requisition.objects.create(
        requester_name="Samuel Addo",
        requester_phone="+233240001122",
        purpose="Youth Camp Supplies",
        description="Purchasing stationery and supplies for annual camp",
        amount_requested=Decimal("1500.00"),
        status=RequisitionStatus.SUBMITTED
    )
    assert req.requisition_number.startswith("REQ-")
    assert len(req.requisition_number) == 10  # REQ-000001
    assert req.amount_requested == Decimal("1500.00")
    assert req.disbursed_amount == Decimal("0.00")
    assert req.approved_amount is None


def test_amounts_remain_separate(system_admin, system_user):
    """Enforce Business Principle 20: Requested, approved, and disbursed amounts remain separate."""
    req = Requisition.objects.create(
        requester_name="Esther Mensah",
        requester_phone="+233200002233",
        purpose="Choir Uniform Material",
        description="Material procurement",
        amount_requested=Decimal("5000.00"),
        status=RequisitionStatus.SUBMITTED
    )

    # Approver approves partial amount
    RequisitionApproval.objects.create(
        requisition=req,
        approver=system_admin,
        decision=ApprovalDecision.APPROVED,
        approved_amount=Decimal("4000.00"),
        remarks="Partial approval per available budget"
    )
    req.status = RequisitionStatus.PENDING_DISBURSEMENT
    req.save()

    # Disburse partial tranche
    RequisitionDisbursement.objects.create(
        requisition=req,
        amount=Decimal("3500.00"),
        method=DisbursementMethod.MOBILE_MONEY,
        recipient_name="Esther Mensah",
        recipient_phone="+233200002233",
        transaction_reference="MM-888999",
        processed_by=system_user
    )

    req.refresh_from_db()
    assert req.amount_requested == Decimal("5000.00")
    assert req.approved_amount == Decimal("4000.00")
    assert req.disbursed_amount == Decimal("3500.00")


def test_rejected_requisition_immutability(system_admin):
    """Enforce Business Principle 25: Rejected requisitions cannot be edited or resubmitted."""
    req = Requisition.objects.create(
        requester_name="Daniel Kyeremeh",
        requester_phone="+233240004455",
        purpose="Sound Equipment",
        description="Microphones purchase",
        amount_requested=Decimal("2000.00"),
        status=RequisitionStatus.REJECTED
    )

    # Attempting to re-submit or edit back to active should raise ValidationError
    req.status = RequisitionStatus.SUBMITTED
    with pytest.raises(ValidationError, match="Rejected requisitions cannot be edited or resubmitted"):
        req.save()


def test_rejection_requires_reason(system_admin):
    req = Requisition.objects.create(
        requester_name="John Doe",
        requester_phone="+233240000000",
        purpose="Test Rejection",
        description="Testing rejection validation",
        amount_requested=Decimal("100.00"),
        status=RequisitionStatus.SUBMITTED
    )

    with pytest.raises(ValidationError, match="reason is mandatory when rejecting"):
        RequisitionApproval.objects.create(
            requisition=req,
            approver=system_admin,
            decision=ApprovalDecision.REJECTED,
            reason=""  # Empty reason
        )


def test_assignment_locking(system_user):
    req = Requisition.objects.create(
        requester_name="John Doe",
        requester_phone="+233240000000",
        purpose="Test Locking",
        description="Testing assignment lock",
        amount_requested=Decimal("100.00"),
        status=RequisitionStatus.SUBMITTED
    )
    assert not req.is_locked

    assignment = RequisitionAssignment.objects.create(
        requisition=req,
        assigned_to=system_user
    )
    req.refresh_from_db()
    assert req.is_locked
    assert req.current_assignment == assignment

    # Releasing assignment
    assignment.release("Completed review")
    req.refresh_from_db()
    assert not req.is_locked
