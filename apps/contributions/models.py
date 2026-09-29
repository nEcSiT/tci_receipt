from decimal import Decimal
from django.db import models
from django.core.exceptions import ValidationError
from django.conf import settings
from apps.core.models import UUIDBaseModel, TimeStampedModel
from apps.core.services.id_generator import IdGenerator
from apps.members.models import Member, TemporaryContributor


class PaymentTransactionStatus(models.TextChoices):
    PENDING = "PENDING", "Pending"
    SUCCESSFUL = "SUCCESSFUL", "Successful"
    FAILED = "FAILED", "Failed"
    CANCELLED = "CANCELLED", "Cancelled"
    DUPLICATE = "DUPLICATE", "Duplicate"


class PaymentMode(models.TextChoices):
    CASH = "CASH", "Cash"
    MOBILE_MONEY = "MOBILE_MONEY", "Mobile Money"
    CHEQUE = "CHEQUE", "Cheque"
    BANK_TRANSACTION = "BANK_TRANSACTION", "Bank Transaction"
    OTHER = "OTHER", "Other"


class EntryMethod(models.TextChoices):
    AUTOMATIC = "AUTOMATIC", "Automatic"
    MANUAL = "MANUAL", "Manual"


class ContributionStatus(models.TextChoices):
    CONFIRMED = "CONFIRMED", "Confirmed"
    PENDING = "PENDING", "Pending"
    CANCELLED = "CANCELLED", "Cancelled"
    FLAGGED = "FLAGGED", "Flagged for Review"


class ContributionType(UUIDBaseModel):
    """
    Church contribution categories (Tithe, Thanksgiving, Higher Life Partners, etc.)
    Table: contribution_types
    """
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "contribution_types"
        verbose_name = "Contribution Type"
        verbose_name_plural = "Contribution Types"
        ordering = ["name"]

    def __str__(self):
        return self.name


class PaymentTransaction(UUIDBaseModel):
    """
    Inbound gateway payment record.
    Table: payment_transactions
    """
    provider = models.CharField(max_length=50)  # MTN_MOMO, PAYSTACK, etc.
    provider_reference = models.CharField(max_length=200, db_index=True)
    transaction_phone = models.CharField(max_length=30, db_index=True)
    provider_name = models.CharField(max_length=200, null=True, blank=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=3, default="GHS")
    status = models.CharField(
        max_length=30,
        choices=PaymentTransactionStatus.choices,
        default=PaymentTransactionStatus.PENDING,
        db_index=True
    )
    failure_code = models.CharField(max_length=100, null=True, blank=True)
    failure_reason = models.TextField(null=True, blank=True)
    provider_data = models.JSONField(null=True, blank=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "payment_transactions"
        verbose_name = "Payment Transaction"
        verbose_name_plural = "Payment Transactions"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.provider} [{self.provider_reference}] {self.amount} {self.currency} ({self.status})"


class Contribution(UUIDBaseModel):
    """
    Core contribution ledger record.
    Table: contributions
    """
    contribution_number = models.CharField(max_length=50, unique=True, db_index=True)
    member = models.ForeignKey(
        Member,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="contributions"
    )
    temporary_contributor = models.ForeignKey(
        TemporaryContributor,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="contributions"
    )
    payment_transaction = models.ForeignKey(
        PaymentTransaction,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="contributions"
    )
    contribution_type = models.ForeignKey(
        ContributionType,
        on_delete=models.PROTECT,
        related_name="contributions"
    )
    custom_type_description = models.TextField(null=True, blank=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=3, default="GHS")
    payment_mode = models.CharField(max_length=40, choices=PaymentMode.choices)
    entry_method = models.CharField(max_length=20, choices=EntryMethod.choices)
    reference_number = models.CharField(max_length=200, null=True, blank=True)
    status = models.CharField(
        max_length=30,
        choices=ContributionStatus.choices,
        default=ContributionStatus.CONFIRMED,
        db_index=True
    )
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="contributions_recorded"
    )

    class Meta:
        db_table = "contributions"
        verbose_name = "Contribution"
        verbose_name_plural = "Contributions"
        ordering = ["-created_at"]
        constraints = [
            # Exactly one contributor identity: member XOR temporary_contributor
            models.CheckConstraint(
                condition=(
                    (models.Q(member__isnull=False) & models.Q(temporary_contributor__isnull=True)) |
                    (models.Q(member__isnull=True) & models.Q(temporary_contributor__isnull=False))
                ),
                name="contribution_contributor_xor"
            ),
            # Automatic contribution requires a payment transaction
            models.CheckConstraint(
                condition=(
                    models.Q(entry_method=EntryMethod.MANUAL) |
                    models.Q(payment_transaction__isnull=False)
                ),
                name="automatic_contribution_requires_transaction"
            ),
        ]

    def clean(self):
        # Enforce XOR contributor
        has_member = self.member_id is not None
        has_temp = self.temporary_contributor_id is not None
        if not (has_member ^ has_temp):
            raise ValidationError("A contribution must reference exactly one contributor identity (member OR temporary contributor).")

        # Automatic requires payment transaction
        if self.entry_method == EntryMethod.AUTOMATIC and not self.payment_transaction_id:
            raise ValidationError("Automatic contributions require an associated payment transaction.")

        # Amount must be strictly positive
        if self.amount is not None and self.amount <= Decimal("0.00"):
            raise ValidationError("Contribution amount must be greater than zero.")

    def save(self, *args, **kwargs):
        if not self.contribution_number:
            self.contribution_number = IdGenerator.generate_contribution_number()
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        contributor = self.member.full_name if self.member else str(self.temporary_contributor)
        return f"{self.contribution_number} - {contributor}: {self.amount} {self.currency}"
