import uuid
from django.db import models
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager
from django.core.exceptions import ValidationError
from django.utils import timezone
from apps.core.models import UUIDBaseModel, TimeStampedModel


class UserManager(BaseUserManager):
    """Custom user manager supporting UUID keys and email authentication."""

    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError("The Email field must be set")
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        """Creates the initial System Administrator."""
        extra_fields.setdefault("is_staff", True)
        user = self.create_user(email, password, **extra_fields)
        # Assign System Administrator role
        admin_role, _ = Role.objects.get_or_create(
            name=Role.SYSTEM_ADMINISTRATOR,
            defaults={"description": "Highest-level application user with system-wide review and administrative access."}
        )
        UserRole.objects.get_or_create(user=user, role=admin_role)
        return user


class User(AbstractBaseUser, UUIDBaseModel):
    """
    Primary user model for internal church staff and administrators.
    Table: users
    """
    email = models.EmailField(max_length=255, unique=True, db_index=True)
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    last_login_at = models.DateTimeField(null=True, blank=True)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["first_name", "last_name"]

    class Meta:
        db_table = "users"
        verbose_name = "User"
        verbose_name_plural = "Users"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.first_name} {self.last_name} ({self.email})"

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}".strip()

    @property
    def is_system_administrator(self) -> bool:
        """Checks if this user is the single System Administrator."""
        return self.user_roles.filter(role__name=Role.SYSTEM_ADMINISTRATOR, role__is_active=True).exists()

    def has_permission(self, permission_code: str) -> bool:
        """Checks if this active user has been granted the given permission code through active roles."""
        if not self.is_active:
            return False
        # System Administrator has all permissions implicitly
        if self.is_system_administrator:
            return True
        return RolePermission.objects.filter(
            role__user_roles__user=self,
            role__is_active=True,
            permission__code=permission_code
        ).exists()

    def get_all_permission_codes(self) -> set[str]:
        if not self.is_active:
            return set()
        if self.is_system_administrator:
            return set(Permission.objects.values_list("code", flat=True))
        return set(RolePermission.objects.filter(
            role__user_roles__user=self,
            role__is_active=True
        ).values_list("permission__code", flat=True))


class Role(UUIDBaseModel):
    """
    Configurable user roles.
    Table: roles
    """
    SYSTEM_ADMINISTRATOR = "System Administrator"
    SYSTEM_USER = "System User"

    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "roles"
        verbose_name = "Role"
        verbose_name_plural = "Roles"
        ordering = ["name"]

    def __str__(self):
        return self.name


class Permission(models.Model):
    """
    Fine-grained system permissions.
    Table: permissions
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=150, unique=True)
    name = models.CharField(max_length=150)
    description = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "permissions"
        verbose_name = "Permission"
        verbose_name_plural = "Permissions"
        ordering = ["code"]

    def __str__(self):
        return f"{self.name} ({self.code})"


class UserRole(models.Model):
    """
    Associates users with roles.
    Table: user_roles
    """
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="user_roles")
    role = models.ForeignKey(Role, on_delete=models.CASCADE, related_name="user_roles")
    assigned_at = models.DateTimeField(auto_now_add=True)
    assigned_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="role_assignments_made")

    class Meta:
        db_table = "user_roles"
        unique_together = ("user", "role")
        verbose_name = "User Role"
        verbose_name_plural = "User Roles"

    def clean(self):
        # Non-negotiable Principle: Exactly one System Administrator exists.
        if self.role.name == Role.SYSTEM_ADMINISTRATOR:
            existing = UserRole.objects.filter(role__name=Role.SYSTEM_ADMINISTRATOR).exclude(user=self.user)
            if existing.exists():
                raise ValidationError("Exactly one System Administrator may exist in the system.")

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.user} -> {self.role.name}"


class RolePermission(models.Model):
    """
    Associates roles with fine-grained permissions.
    Table: role_permissions
    """
    role = models.ForeignKey(Role, on_delete=models.CASCADE, related_name="role_permissions")
    permission = models.ForeignKey(Permission, on_delete=models.CASCADE, related_name="role_permissions")
    assigned_at = models.DateTimeField(auto_now_add=True)
    assigned_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="permission_assignments_made")

    class Meta:
        db_table = "role_permissions"
        unique_together = ("role", "permission")
        verbose_name = "Role Permission"
        verbose_name_plural = "Role Permissions"

    def __str__(self):
        return f"{self.role.name} -> {self.permission.code}"


class PasswordResetToken(UUIDBaseModel):
    """
    Secure, expiring, single-use credentials for password resets.
    """
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="password_reset_tokens")
    token_hash = models.CharField(max_length=128, unique=True)
    expires_at = models.DateTimeField()
    is_used = models.BooleanField(default=False)
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "password_reset_tokens"
        ordering = ["-created_at"]

    @property
    def is_valid(self) -> bool:
        return not self.is_used and self.expires_at > timezone.now()


class AdminRecoverySession(UUIDBaseModel):
    """
    Tracks multi-factor recovery session for the single System Administrator.
    Validates Email OTP followed by Phone OTP against protected secrets.
    """
    session_token = models.CharField(max_length=128, unique=True)
    email_otp_hash = models.CharField(max_length=128)
    email_verified = models.BooleanField(default=False)
    phone_otp_hash = models.CharField(max_length=128)
    phone_verified = models.BooleanField(default=False)
    expires_at = models.DateTimeField()
    is_completed = models.BooleanField(default=False)

    class Meta:
        db_table = "admin_recovery_sessions"
        ordering = ["-created_at"]

    @property
    def is_valid(self) -> bool:
        return not self.is_completed and self.expires_at > timezone.now()

    @property
    def is_ready_for_reset(self) -> bool:
        return self.is_valid and self.email_verified and self.phone_verified

