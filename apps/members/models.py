from django.db import models
from django.core.exceptions import ValidationError
from django.conf import settings
from apps.core.models import UUIDBaseModel, TimeStampedModel
from apps.core.services.id_generator import IdGenerator


class MemberStatus(models.TextChoices):
    ACTIVE = "ACTIVE", "Active"
    INACTIVE = "INACTIVE", "Inactive"
    DEACTIVATED = "DEACTIVATED", "Deactivated"


class TemporaryContributorStatus(models.TextChoices):
    UNVERIFIED = "UNVERIFIED", "Unverified"
    UNDER_REVIEW = "UNDER_REVIEW", "Under Review"
    LINKED = "LINKED", "Linked"
    CONVERTED = "CONVERTED", "Converted"


class MemberMergeStatus(models.TextChoices):
    PENDING = "PENDING", "Pending"
    APPROVED = "APPROVED", "Approved"
    REJECTED = "REJECTED", "Rejected"


class Member(UUIDBaseModel):
    """
    Permanent church member entity.
    Table: members
    """
    member_number = models.CharField(max_length=50, unique=True, db_index=True)
    first_name = models.CharField(max_length=100)
    middle_name = models.CharField(max_length=100, null=True, blank=True)
    last_name = models.CharField(max_length=100)
    email = models.EmailField(max_length=255, null=True, blank=True, db_index=True)
    ministry = models.CharField(max_length=150, null=True, blank=True)
    team = models.CharField(max_length=150, null=True, blank=True)
    department = models.CharField(max_length=150, null=True, blank=True)
    status = models.CharField(
        max_length=30,
        choices=MemberStatus.choices,
        default=MemberStatus.ACTIVE,
        db_index=True
    )

    class Meta:
        db_table = "members"
        verbose_name = "Member"
        verbose_name_plural = "Members"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.first_name} {self.last_name} ({self.member_number})"

    @property
    def full_name(self):
        names = [self.first_name, self.middle_name, self.last_name]
        return " ".join(n for n in names if n)

    def save(self, *args, **kwargs):
        if not self.member_number:
            self.member_number = IdGenerator.generate_member_number()
        super().save(*args, **kwargs)


class MemberPhoneNumber(UUIDBaseModel):
    """
    Phone number associated with a member.
    Enforces active phone uniqueness across members and one primary phone per member.
    Table: member_phone_numbers
    """
    member = models.ForeignKey(Member, on_delete=models.CASCADE, related_name="phone_numbers")
    phone_number = models.CharField(max_length=30, db_index=True)
    provider = models.CharField(max_length=50, null=True, blank=True)
    is_primary = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "member_phone_numbers"
        verbose_name = "Member Phone Number"
        verbose_name_plural = "Member Phone Numbers"
        constraints = [
            models.UniqueConstraint(
                fields=["phone_number"],
                condition=models.Q(is_active=True),
                name="unique_active_phone_across_members"
            ),
            models.UniqueConstraint(
                fields=["member"],
                condition=models.Q(is_active=True, is_primary=True),
                name="unique_active_primary_phone_per_member"
            ),
        ]

    def clean(self):
        if self.is_active:
            # Check for conflict with other members' active phones
            conflict = MemberPhoneNumber.objects.filter(
                phone_number=self.phone_number,
                is_active=True
            ).exclude(pk=self.pk)
            if conflict.exists():
                other_member = conflict.first().member
                raise ValidationError(
                    f"Phone number {self.phone_number} is already actively assigned to member {other_member}."
                )

            # Check if setting as primary violates the one primary rule
            if self.is_primary:
                primary_conflict = MemberPhoneNumber.objects.filter(
                    member=self.member,
                    is_active=True,
                    is_primary=True
                ).exclude(pk=self.pk)
                if primary_conflict.exists():
                    raise ValidationError("Member already has an active primary phone number.")

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.phone_number} ({self.member.member_number})"


class TemporaryContributor(UUIDBaseModel):
    """
    Unverified contributor created when an automatic payment's phone does not match any existing member.
    Table: temporary_contributors
    """
    phone_number = models.CharField(max_length=30, db_index=True)
    provider = models.CharField(max_length=50, null=True, blank=True)
    provider_name = models.CharField(max_length=200, null=True, blank=True)
    provider_account_reference = models.CharField(max_length=200, null=True, blank=True)
    first_name = models.CharField(max_length=100, null=True, blank=True)
    last_name = models.CharField(max_length=100, null=True, blank=True)
    status = models.CharField(
        max_length=30,
        choices=TemporaryContributorStatus.choices,
        default=TemporaryContributorStatus.UNVERIFIED,
        db_index=True
    )
    converted_member = models.ForeignKey(
        Member,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="converted_from_temporary"
    )

    class Meta:
        db_table = "temporary_contributors"
        verbose_name = "Temporary Contributor"
        verbose_name_plural = "Temporary Contributors"
        ordering = ["-created_at"]

    def __str__(self):
        name = f"{self.first_name} {self.last_name}".strip() or self.provider_name or "Unknown"
        return f"{name} ({self.phone_number}) [{self.status}]"


class MemberMergeRequest(UUIDBaseModel):
    """
    Merge request between two members.
    Requires System Administrator approval.
    Table: member_merge_requests
    """
    source_member = models.ForeignKey(Member, on_delete=models.CASCADE, related_name="merge_requests_as_source")
    target_member = models.ForeignKey(Member, on_delete=models.CASCADE, related_name="merge_requests_as_target")
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="merge_requests_created")
    reason = models.TextField()
    status = models.CharField(
        max_length=30,
        choices=MemberMergeStatus.choices,
        default=MemberMergeStatus.PENDING,
        db_index=True
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="merge_requests_reviewed"
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_remarks = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "member_merge_requests"
        verbose_name = "Member Merge Request"
        verbose_name_plural = "Member Merge Requests"
        ordering = ["-created_at"]

    def clean(self):
        if self.source_member == self.target_member:
            raise ValidationError("Source member and target member cannot be identical.")

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Merge {self.source_member} -> {self.target_member} [{self.status}]"
