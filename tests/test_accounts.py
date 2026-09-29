import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone
from datetime import timedelta
from apps.accounts.models import User, Role, UserRole, PasswordResetToken


def test_single_system_administrator_constraint(seed_data, system_admin):
    """Enforce Business Principle 1 & 5: Exactly one System Administrator exists."""
    second_user = User.objects.create_user(
        email="second_admin@tcihlc.org",
        password="ValidPassword123!",
        first_name="Second",
        last_name="Admin"
    )
    admin_role = Role.objects.get(name=Role.SYSTEM_ADMINISTRATOR)

    with pytest.raises(ValidationError, match="Exactly one System Administrator may exist"):
        UserRole.objects.create(user=second_user, role=admin_role)


def test_permission_checks(system_admin, system_user):
    """Verify fine-grained permission enforcement."""
    # System administrator has all permissions
    assert system_admin.has_permission("audit.view")
    assert system_admin.has_permission("contribution.create")

    # System user has default granted permissions
    assert system_user.has_permission("contribution.create")
    assert system_user.has_permission("requisition.view")

    # System user is denied ungranted permissions (e.g., audit.view, user.manage)
    assert not system_user.has_permission("audit.view")
    assert not system_user.has_permission("user.manage")


def test_deactivated_user_permission_denial(system_user):
    """Deactivated users must be denied permissions."""
    system_user.is_active = False
    system_user.save()
    assert not system_user.has_permission("contribution.create")


def test_password_reset_token_validity(system_user):
    """Expiring, single-use password reset tokens."""
    token = PasswordResetToken.objects.create(
        user=system_user,
        token_hash="sample_hash_123",
        expires_at=timezone.now() + timedelta(minutes=15)
    )
    assert token.is_valid is True

    # Mark as used
    token.is_used = True
    token.save()
    assert token.is_valid is False


def test_alternate_administrator_creation_paths_are_disabled(seed_data, system_admin):
    """No alternate Django staff/superuser path may create another administrator."""
    with pytest.raises(ValueError, match="superuser creation is disabled"):
        User.objects.create_superuser(
            email="second.superadmin@tcihlc.org",
            password="ValidPassword123!",
            first_name="Second",
            last_name="Administrator",
        )

    with pytest.raises(ValueError, match="staff/admin accounts are disabled"):
        User.objects.create_user(
            email="second.staff@tcihlc.org",
            password="ValidPassword123!",
            first_name="Second",
            last_name="Staff",
            is_staff=True,
        )

    assert User.objects.filter(is_staff=True).count() == 0
    assert UserRole.objects.filter(role__name=Role.SYSTEM_ADMINISTRATOR).count() == 1


def test_user_cannot_persist_django_staff_flag(system_admin):
    """Django's staff flag cannot be used as a second privileged identity."""
    system_admin.is_staff = True
    system_admin.save(update_fields=["is_staff"])
    system_admin.refresh_from_db()

    assert system_admin.is_staff is False
    assert system_admin.is_system_administrator is True
