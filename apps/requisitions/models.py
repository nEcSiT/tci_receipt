from decimal import Decimal
from django.db import models
from django.core.exceptions import ValidationError
from django.conf import settings
from django.utils import timezone
from apps.core.models import UUIDBaseModel, TimeStampedModel
from apps.core.services.id_generator import IdGenerator
from apps.members.models import Member


class RequisitionStatus(models.TextChoices):
    DRAFT = "DRAFT", "Draft"
    SUBMITTED = "SUBMITTED", "Submitted"
    PENDING_REVIEW = "PENDING_REVIEW", "Pending Review"
    UNDER_REVIEW = "UNDER_REVIEW", "Under Review"
    APPROVED = "APPROVED", "Approved"
    REJECTED = "REJECTED", "Rejected"
    PENDING_DISBURSEMENT = "PENDING_DISBURSEMENT", "Pending Disbursement"
    DISBURSED = "DISBURSED", "Disbursed"
    EVIDENCE_SUBMITTED = "EVIDENCE_SUBMITTED", "Evidence Submitted"
    UNDER_VERIFICATION = "UNDER_VERIFICATION", "Under Verification"
    COMPLETED = "COMPLETED", "Completed"


class PreferredDisbursementMethod(models.TextChoices):
    CASH = "CASH", "Cash"
    MOBILE_MONEY = "MOBILE_MONEY", "Mobile Money"
    BANK_TRANSFER = "BANK_TRANSFER", "Bank Transfer"
    OTHER = "OTHER", "Other"


class DisbursementMethod(models.TextChoices):
    CASH = "CASH", "Cash"
    MOBILE_MONEY = "MOBILE_MONEY", "Mobile Money"
    BANK_TRANSFER = "BANK_TRANSFER", "Bank Transfer"
    OTHER = "OTHER", "Other"


class ApprovalDecision(models.TextChoices):
    APPROVED = "APPROVED", "Approved"
    REJECTED = "REJECTED", "Rejected"


class EvidenceStatus(models.TextChoices):
    UPLOADED = "UPLOADED", "Uploaded"
    UNDER_REVIEW = "UNDER_REVIEW", "Under Review"
    VERIFIED = "VERIFIED", "Verified"
    REJECTED = "REJECTED", "Rejected"


class Requisition(UUIDBaseModel):
    """
    Core requisition entity tracking requested, approved, and disbursed amounts separately.
    Table: requisitions
    """
    requisition_number = models.CharField(max_length=50, unique=True, db_index=True)
    requester_name = models.CharField(max_length=200)
    requester_phone = models.CharField(max_length=30, db_index=True)
    requester_member = models.ForeignKey(
        Member,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="requisitions"
    )
    ministry = models.CharField(max_length=150, null=True, blank=True)
    team = models.CharField(max_length=150, null=True, blank=True)
    department = models.CharField(max_length=150, null=True, blank=True)
    group_name = models.CharField(max_length=150, null=True, blank=True)
    purpose = models.CharField(max_length=255)
    description = models.TextField()
    amount_requested = models.DecimalField(max_digits=12, decimal_places=2)
    date_funds_needed = models.DateField(null=True, blank=True)
    preferred_disbursement_method = models.CharField(
        max_length=30,
        choices=PreferredDisbursementMethod.choices,
        null=True,
        blank=True
    )
    preferred_disbursement_details = models.TextField(null=True, blank=True)
    remarks = models.TextField(null=True, blank=True)
    status = models.CharField(
        max_length=40,
        choices=RequisitionStatus.choices,
        default=RequisitionStatus.DRAFT,
        db_index=True
    )
    related_requisition = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="referenced_by"
    )
    submitted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "requisitions"
        verbose_name = "Requisition"
        verbose_name_plural = "Requisitions"
        ordering = ["-created_at"]

    def clean(self):
        if self.amount_requested is not None and self.amount_requested <= Decimal("0.00"):
            raise ValidationError("Requested amount must be strictly greater than zero.")

        # Business Rule 25: Rejected requisitions cannot be edited or resubmitted.
        if self.pk and not self._state.adding:
            original = Requisition.objects.filter(pk=self.pk).first()
            if original and original.status == RequisitionStatus.REJECTED and self.status != RequisitionStatus.REJECTED:
                raise ValidationError("Rejected requisitions cannot be edited or resubmitted. A new requisition is required.")

    def save(self, *args, **kwargs):
        if not self.requisition_number:
            self.requisition_number = IdGenerator.generate_requisition_number()
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.requisition_number} - {self.purpose} [{self.status}]"

    @property
    def current_assignment(self):
        return self.assignments.filter(is_current=True).first()

    @property
    def is_locked(self) -> bool:
        return self.current_assignment is not None

    @property
    def latest_approval(self):
        return self.approvals.order_by("-created_at").first()

    @property
    def latest_disbursement(self):
        return self.disbursements.order_by("-disbursed_at").first()

    @property
    def approved_amount(self) -> Decimal | None:
        approval = self.latest_approval
        return approval.approved_amount if approval and approval.decision == ApprovalDecision.APPROVED else None

    @property
    def disbursed_amount(self) -> Decimal:
        return self.disbursements.aggregate(total=models.Sum("amount"))["total"] or Decimal("0.00")


class RequisitionAttachment(UUIDBaseModel):
    """
    Initial supporting documents uploaded when drafting or submitting a requisition.
    Table: requisition_attachments
    """
    requisition = models.ForeignKey(Requisition, on_delete=models.CASCADE, related_name="attachments")
    uploaded_by_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="requisition_attachments"
    )
    uploader_name = models.CharField(max_length=200)
    uploader_phone = models.CharField(max_length=30, null=True, blank=True)
    file_name = models.CharField(max_length=255)
    file_type = models.CharField(max_length=100)
    storage_key = models.CharField(max_length=500)
    file_size = models.BigIntegerField()

    class Meta:
        db_table = "requisition_attachments"
        verbose_name = "Requisition Attachment"
        verbose_name_plural = "Requisition Attachments"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.file_name} ({self.requisition.requisition_number})"


class RequisitionAssignment(UUIDBaseModel):
    """
    Assignment record for locking concurrent review when a user clicks 'Start Working'.
    Table: requisition_assignments
    """
    requisition = models.ForeignKey(Requisition, on_delete=models.CASCADE, related_name="assignments")
    assigned_to = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="assigned_requisitions")
    assigned_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="assignments_granted")
    assigned_at = models.DateTimeField(default=timezone.now)
    released_at = models.DateTimeField(null=True, blank=True)
    release_reason = models.TextField(null=True, blank=True)
    is_current = models.BooleanField(default=True)

    class Meta:
        db_table = "requisition_assignments"
        verbose_name = "Requisition Assignment"
        verbose_name_plural = "Requisition Assignments"
        ordering = ["-assigned_at"]

    def release(self, reason: str):
        self.is_current = False
        self.released_at = timezone.now()
        self.release_reason = reason
        self.save(update_fields=["is_current", "released_at", "release_reason", "updated_at"])


class RequisitionApproval(UUIDBaseModel):
    """
    Official review decision (Approved or Rejected) on a requisition.
    Table: requisition_approvals
    """
    requisition = models.ForeignKey(Requisition, on_delete=models.CASCADE, related_name="approvals")
    approver = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="requisition_approvals_given")
    decision = models.CharField(max_length=20, choices=ApprovalDecision.choices)
    approved_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    reason = models.TextField(null=True, blank=True)
    remarks = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "requisition_approvals"
        verbose_name = "Requisition Approval"
        verbose_name_plural = "Requisition Approvals"
        ordering = ["-created_at"]

    def clean(self):
        if self.decision == ApprovalDecision.REJECTED and not self.reason:
            raise ValidationError("A reason is mandatory when rejecting a requisition.")
        if self.decision == ApprovalDecision.APPROVED and self.approved_amount is None:
            raise ValidationError("An approved amount must be specified when approving a requisition.")

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.requisition.requisition_number} - {self.decision} by {self.approver}"


class RequisitionDisbursement(UUIDBaseModel):
    """
    Actual fund release record.
    Table: requisition_disbursements
    """
    requisition = models.ForeignKey(Requisition, on_delete=models.PROTECT, related_name="disbursements")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    method = models.CharField(max_length=30, choices=DisbursementMethod.choices)
    recipient_name = models.CharField(max_length=200)
    recipient_phone = models.CharField(max_length=30, null=True, blank=True)
    transaction_reference = models.CharField(max_length=200, null=True, blank=True)
    processed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="disbursements_processed")
    disbursed_at = models.DateTimeField(default=timezone.now)
    remarks = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "requisition_disbursements"
        verbose_name = "Requisition Disbursement"
        verbose_name_plural = "Requisition Disbursements"
        ordering = ["-disbursed_at"]

    def clean(self):
        if self.amount <= Decimal("0.00"):
            raise ValidationError("Disbursed amount must be greater than zero.")

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.requisition.requisition_number} disbursed {self.amount} via {self.method} to {self.recipient_name}"


class RequisitionEvidence(UUIDBaseModel):
    """
    Post-disbursement proof of expenditure.
    Multiple evidence files are supported.
    Table: requisition_evidence
    """
    requisition = models.ForeignKey(Requisition, on_delete=models.CASCADE, related_name="evidence_files")
    uploaded_by_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="requisition_evidence_uploaded"
    )
    uploader_name = models.CharField(max_length=200)
    uploader_phone = models.CharField(max_length=30, null=True, blank=True)
    file_name = models.CharField(max_length=255)
    file_type = models.CharField(max_length=100)
    storage_key = models.CharField(max_length=500)
    file_size = models.BigIntegerField()
    status = models.CharField(
        max_length=30,
        choices=EvidenceStatus.choices,
        default=EvidenceStatus.UPLOADED,
        db_index=True
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="evidence_reviews_performed"
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_remarks = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "requisition_evidence"
        verbose_name = "Requisition Evidence"
        verbose_name_plural = "Requisition Evidence Files"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.file_name} for {self.requisition.requisition_number} [{self.status}]"
