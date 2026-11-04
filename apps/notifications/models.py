from django.db import models
from django.conf import settings
from apps.core.models import UUIDBaseModel, TimeStampedModel
from apps.contributions.models import ContributionType


class NotificationType(models.TextChoices):
    THANK_YOU = "THANK_YOU", "Thank You"
    RECEIPT = "RECEIPT", "Receipt"
    PAYMENT_FAILURE = "PAYMENT_FAILURE", "Payment Failure"
    REQUISITION_APPROVED = "REQUISITION_APPROVED", "Requisition Approved"
    REQUISITION_REJECTED = "REQUISITION_REJECTED", "Requisition Rejected"
    READY_FOR_DISBURSEMENT = "READY_FOR_DISBURSEMENT", "Ready for Disbursement"
    PROOF_UPLOAD = "PROOF_UPLOAD", "Proof Upload Request"
    EVIDENCE_REJECTED = "EVIDENCE_REJECTED", "Evidence Rejected"
    RECEIPT_GENERATION_FAILED = "RECEIPT_GENERATION_FAILED", "Receipt Generation Failed"
    THANK_YOU_FAILED = "THANK_YOU_FAILED", "Thank You Failed"
    FAILED_NOTIFICATION = "FAILED_NOTIFICATION", "Failed Notification"
    MERCHANT_TRANSACTION = "MERCHANT_TRANSACTION", "Merchant Transaction"
    DUPLICATE_TRANSACTION = "DUPLICATE_TRANSACTION", "Duplicate Transaction"
    OTHER = "OTHER", "Other"


class NotificationStatus(models.TextChoices):
    PENDING = "PENDING", "Pending"
    SENDING = "SENDING", "Sending"
    SENT = "SENT", "Sent"
    DELIVERED = "DELIVERED", "Delivered"
    FAILED = "FAILED", "Failed"
    RETRYING = "RETRYING", "Retrying"
    MANUAL_ACTION_REQUIRED = "MANUAL_ACTION_REQUIRED", "Manual Action Required"


class ManualActionType(models.TextChoices):
    RECEIPT_GENERATION_FAILED = "RECEIPT_GENERATION_FAILED", "Receipt Generation Failed"
    THANK_YOU_NOTIFICATION_FAILED = "THANK_YOU_NOTIFICATION_FAILED", "Thank You Notification Failed"
    MERCHANT_TRANSACTION = "MERCHANT_TRANSACTION", "Merchant Transaction"
    DUPLICATE_TRANSACTION_REVIEW = "DUPLICATE_TRANSACTION_REVIEW", "Duplicate Transaction Review"
    UNKNOWN_CONTRIBUTOR_REVIEW = "UNKNOWN_CONTRIBUTOR_REVIEW", "Unknown Contributor Review"
    OTHER = "OTHER", "Other"


class ManualActionPriority(models.TextChoices):
    LOW = "LOW", "Low"
    MEDIUM = "MEDIUM", "Medium"
    HIGH = "HIGH", "High"
    CRITICAL = "CRITICAL", "Critical"


class ManualActionStatus(models.TextChoices):
    OPEN = "OPEN", "Open"
    ASSIGNED = "ASSIGNED", "Assigned"
    IN_PROGRESS = "IN_PROGRESS", "In Progress"
    RESOLVED = "RESOLVED", "Resolved"


class NotificationTemplate(UUIDBaseModel):
    """
    Configurable message templates supporting variable substitution.
    Table: notification_templates
    """
    name = models.CharField(max_length=150, unique=True)
    notification_type = models.CharField(max_length=50, choices=NotificationType.choices)
    contribution_type = models.ForeignKey(
        ContributionType,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="notification_templates"
    )
    template_body = models.TextField()
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="templates_created"
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="templates_updated"
    )

    class Meta:
        db_table = "notification_templates"
        verbose_name = "Notification Template"
        verbose_name_plural = "Notification Templates"
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.notification_type})"


class Notification(UUIDBaseModel):
    """
    Outbound notification record. Rendered message is stored.
    Table: notifications
    """
    notification_type = models.CharField(max_length=50, choices=NotificationType.choices)
    recipient_name = models.CharField(max_length=200, null=True, blank=True)
    recipient_phone = models.CharField(max_length=30, db_index=True)
    related_record_type = models.CharField(max_length=100, null=True, blank=True)
    related_record_id = models.UUIDField(null=True, blank=True)
    channel = models.CharField(max_length=20, default="SMS")
    template = models.ForeignKey(
        NotificationTemplate,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="notifications"
    )
    message = models.TextField()
    status = models.CharField(
        max_length=30,
        choices=NotificationStatus.choices,
        default=NotificationStatus.PENDING,
        db_index=True
    )
    sent_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    attempt_count = models.IntegerField(default=0)
    provider_reference = models.CharField(max_length=200, null=True, blank=True)
    failure_reason = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "notifications"
        verbose_name = "Notification"
        verbose_name_plural = "Notifications"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.notification_type} -> {self.recipient_phone} [{self.status}]"


class NotificationAttempt(UUIDBaseModel):
    """
    Individual attempt log for an outbound notification.
    Table: notification_attempts
    """
    notification = models.ForeignKey(Notification, on_delete=models.CASCADE, related_name="attempts")
    attempt_number = models.IntegerField()
    provider_reference = models.CharField(max_length=200, null=True, blank=True)
    provider_response = models.JSONField(null=True, blank=True)
    status = models.CharField(max_length=30)
    failure_code = models.CharField(max_length=100, null=True, blank=True)
    failure_reason = models.TextField(null=True, blank=True)
    attempted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "notification_attempts"
        verbose_name = "Notification Attempt"
        verbose_name_plural = "Notification Attempts"
        ordering = ["-attempted_at"]

    def __str__(self):
        return f"Attempt #{self.attempt_number} for Notification {self.notification.id} [{self.status}]"


class ManualAction(UUIDBaseModel):
    """
    Action item for items requiring human intervention after automated attempts fail.
    Table: manual_actions
    """
    action_type = models.CharField(max_length=100, choices=ManualActionType.choices)
    related_record_type = models.CharField(max_length=100, null=True, blank=True)
    related_record_id = models.UUIDField(null=True, blank=True)
    description = models.TextField()
    attempt_count = models.IntegerField(default=0)
    priority = models.CharField(max_length=20, choices=ManualActionPriority.choices, default=ManualActionPriority.MEDIUM)
    status = models.CharField(max_length=30, choices=ManualActionStatus.choices, default=ManualActionStatus.OPEN)
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="manual_actions_assigned"
    )
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="manual_actions_resolved"
    )
    resolution_notes = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "manual_actions"
        verbose_name = "Manual Action"
        verbose_name_plural = "Manual Actions"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.action_type} [{self.priority}] - {self.status}"
