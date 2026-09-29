import datetime
import pytest
from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User, Role, UserRole, Permission, RolePermission, PasswordResetToken, AdminRecoverySession
from apps.accounts.services import AuthService, AdminRecoveryService, UserService, RolePermissionService
from apps.audit.models import AuditLog
from apps.audit.services import AuditService


def test_m2_bt_001_system_admin_login(client, seed_data, system_admin):
    """M2-BT-001: System Administrator login reaches dashboard."""
    response = client.post(reverse("accounts:login"), {
        "email": settings.SYSTEM_ADMIN_EMAIL,
        "password": settings.SYSTEM_ADMIN_INITIAL_PASSWORD,
    }, follow=True)

    assert response.status_code == 200
    assert response.redirect_chain[-1][0] == reverse("core:dashboard")
    assert "Dashboard Overview" in response.content.decode()
    assert "System Administrator" in response.content.decode()
    assert AuditLog.objects.filter(action="AUTH_LOGIN_SUCCESS", entity_id=system_admin.id).exists()


def test_m2_bt_002_invalid_login_generic_failure(client, seed_data, system_admin):
    """M2-BT-002: Invalid login fails without revealing account existence."""
    # 1. Real email, wrong password
    resp1 = client.post(reverse("accounts:login"), {
        "email": settings.SYSTEM_ADMIN_EMAIL,
        "password": "WrongPassword123!",
    })
    assert resp1.status_code == 401
    assert "Invalid email or password." in resp1.content.decode()

    # 2. Non-existent email
    resp2 = client.post(reverse("accounts:login"), {
        "email": "nonexistent_person@tcihlc.org",
        "password": "WrongPassword123!",
    })
    assert resp2.status_code == 401
    assert "Invalid email or password." in resp2.content.decode()

    # Both produce audit failure records
    assert AuditLog.objects.filter(action="AUTH_LOGIN_FAILED").count() >= 2


def test_m2_bt_003_system_user_login(client, seed_data, system_user):
    """M2-BT-003: System User login reaches dashboard."""
    response = client.post(reverse("accounts:login"), {
        "email": "officer@tcihlc.org",
        "password": "ValidPassword123!",
    }, follow=True)

    assert response.status_code == 200
    assert response.redirect_chain[-1][0] == reverse("core:dashboard")
    content = response.content.decode()
    assert "Finance" in content
    assert AuditLog.objects.filter(action="AUTH_LOGIN_SUCCESS", entity_id=system_user.id).exists()


def test_m2_bt_004_inactive_user_login_rejected(client, seed_data, system_user):
    """M2-BT-004: Inactive user login is rejected."""
    system_user.is_active = False
    system_user.save()

    response = client.post(reverse("accounts:login"), {
        "email": system_user.email,
        "password": "ValidPassword123!",
    })

    assert response.status_code == 400
    assert "inactive" in response.content.decode().lower()
    assert not response.wsgi_request.user.is_authenticated
    assert AuditLog.objects.filter(action="AUTH_LOGIN_FAILED", entity_id=system_user.id).exists()


def test_m2_bt_005_authorized_creation_of_system_user(client, seed_data, system_admin):
    """M2-BT-005: Authorized creation of System User using an admin-created role."""
    role = Role.objects.create(name="Finance Clerk", description="Test role")
    client.force_login(system_admin)

    response = client.post(reverse("accounts:user_create"), {
        "email": "new.clerk@tcihlc.org",
        "first_name": "Daniel",
        "last_name": "Ansah",
        "role_name": role.name,
        "password": "SecurePassword123!",
    }, follow=True)

    assert response.status_code == 200
    new_user = User.objects.get(email="new.clerk@tcihlc.org")
    assert new_user.first_name == "Daniel"
    assert new_user.user_roles.filter(role=role).exists()
    assert AuditLog.objects.filter(action="USER_CREATED", entity_id=new_user.id).exists()


def test_m2_bt_006_role_and_permission_assignment(seed_data, system_admin, system_user):
    """M2-BT-006: Role and permission assignment updates user capabilities."""
    assert not system_user.has_permission("audit.view")

    # Assign audit.view to the user's custom role
    user_role = system_user.user_roles.first().role
    audit_perm = Permission.objects.get(code="audit.view")
    RolePermission.objects.create(role=user_role, permission=audit_perm, assigned_by=system_admin)

    # Capability takes effect
    assert system_user.has_permission("audit.view")


def test_m2_bt_007_unauthorized_action_blocked(client, seed_data, system_user):
    """M2-BT-007: Unauthorized action blocked by backend security layer."""
    client.force_login(system_user)

    # System User does not have "user.manage" permission -> 403 Forbidden
    resp1 = client.get(reverse("accounts:user_list"))
    assert resp1.status_code == 403

    resp2 = client.get(reverse("accounts:user_create"))
    assert resp2.status_code == 403


def test_m2_bt_008_user_deactivation_preserves_history(client, seed_data, system_admin, system_user):
    """M2-BT-008: User deactivation prevents authentication while preserving history."""
    client.force_login(system_admin)

    # Deactivate user
    response = client.post(reverse("accounts:user_toggle_status", kwargs={"user_id": system_user.id}), {
        "action": "deactivate",
        "reason": "Temporary sabbatical leave",
    }, follow=True)

    assert response.status_code == 200
    system_user.refresh_from_db()
    assert system_user.is_active is False

    # Historical record is preserved in database
    assert User.objects.filter(id=system_user.id).exists()
    assert AuditLog.objects.filter(action="USER_DEACTIVATED", entity_id=system_user.id).exists()

    # Deactivated user cannot log in
    client.logout()
    login_resp = client.post(reverse("accounts:login"), {
        "email": system_user.email,
        "password": "ValidPassword123!",
    })
    assert login_resp.status_code == 400


def test_m2_bt_009_password_reset_single_use(client, seed_data, system_user):
    """M2-BT-009: Valid password reset token permits exactly one reset."""
    success, raw_token = AuthService.request_password_reset(system_user.email)
    assert success is True
    assert raw_token is not None

    # First reset succeeds
    new_pw = "BrandNewPassword123!"
    success, error = AuthService.confirm_password_reset(raw_token, new_pw)
    assert success is True
    assert error is None

    # Verify user can log in with new password
    login_resp = client.post(reverse("accounts:login"), {
        "email": system_user.email,
        "password": new_pw,
    })
    assert login_resp.status_code == 302

    # Second reset attempt with the same token is rejected
    success_second, error_second = AuthService.confirm_password_reset(raw_token, "AnotherNewPassword123!")
    assert success_second is False
    assert "invalid or has expired" in error_second.lower()


def test_m2_bt_010_expired_password_reset_token_rejected(seed_data, system_user):
    """M2-BT-010: Expired password reset token is rejected."""
    success, raw_token = AuthService.request_password_reset(system_user.email)
    assert success is True

    # Manually backdate token expiry
    token_record = PasswordResetToken.objects.filter(user=system_user).first()
    token_record.expires_at = timezone.now() - datetime.timedelta(hours=2)
    token_record.save()

    success_reset, error = AuthService.confirm_password_reset(raw_token, "NewPassword123!")
    assert success_reset is False
    assert "expired" in error.lower()


def test_m2_bt_011_admin_recovery_mfa_workflow(client, seed_data, system_admin):
    """M2-BT-011: System Administrator recovery via dynamic Email OTP + Phone OTP."""
    # 1. Initiate recovery
    session, email_otp, phone_otp, err = AdminRecoveryService.initiate_recovery(settings.SYSTEM_ADMIN_EMAIL)
    assert session is not None
    assert err is None
    assert not session.email_verified
    assert not session.phone_verified

    # Invalid OTP fails
    ok, err_invalid = AdminRecoveryService.verify_email_otp(session.session_token, "000000")
    assert ok is False
    assert "Invalid Email OTP" in err_invalid

    # 2. Verify Email OTP
    ok_email, _ = AdminRecoveryService.verify_email_otp(session.session_token, email_otp)
    assert ok_email is True

    # Phone OTP cannot be skipped or verified out of order
    ok_phone, _ = AdminRecoveryService.verify_phone_otp(session.session_token, phone_otp)
    assert ok_phone is True

    # 3. Complete password reset
    new_admin_pw = "SuperSecureAdminPassword123!"
    ok_reset, reset_err = AdminRecoveryService.complete_recovery(session.session_token, new_admin_pw)
    assert ok_reset is True
    assert reset_err is None

    # Admin can log in with new password
    login_resp = client.post(reverse("accounts:login"), {
        "email": settings.SYSTEM_ADMIN_EMAIL,
        "password": new_admin_pw,
    })
    assert login_resp.status_code == 302


def test_m2_bt_012_immutable_audit_logging(seed_data, system_admin):
    """M2-BT-012: All security and administrative actions produce immutable audit records."""
    log_entry = AuditService.log(
        action="SYSTEM_INIT",
        entity_type="System",
        entity_id=system_admin.id,
        user=system_admin,
        reason="Initial startup",
    )
    assert AuditLog.objects.count() > 0

    # Attempting to mutate an audit record raises PermissionDenied
    log_entry.action = "TAMPERED_ACTION"
    with pytest.raises(PermissionDenied, match="Audit records are immutable"):
        log_entry.save()

    # Attempting to delete an audit record raises PermissionDenied
    with pytest.raises(PermissionDenied, match="Audit records are append-only"):
        log_entry.delete()


def test_m2_bt_013_system_user_cannot_create_system_administrator(seed_data, system_user):
    """M2-BT-013: System User cannot create or assign the System Administrator role."""
    with pytest.raises(ValidationError, match="System Administrator role"):
        UserService.create_system_user(
            creator=system_user,
            email="attempted.admin@tcihlc.org",
            first_name="Attempted",
            last_name="Admin",
            role_name=Role.SYSTEM_ADMINISTRATOR,
            initial_password="SecurePassword123!",
        )


def test_m2_bt_014_user_management_is_enforced_in_service_layer(seed_data, system_user, system_admin):
    """M2-BT-014: User management cannot be bypassed by calling services directly."""
    with pytest.raises(ValidationError, match="permission"):
        UserService.create_system_user(
            creator=system_user,
            email="unauthorized@tcihlc.org",
            first_name="Unauthorized",
            last_name="Creator",
            role_name=system_user.user_roles.first().role.name,
            initial_password="SecurePassword123!",
        )

    with pytest.raises(ValidationError, match="permission"):
        UserService.update_system_user(
            actor=system_user,
            user=system_admin,
            first_name="Changed",
            last_name="Admin",
            role_name=system_user.user_roles.first().role.name,
        )


def test_m2_bt_015_system_admin_can_configure_custom_role_permissions(client, seed_data, system_admin, system_user):
    """M2-BT-015: System Administrator can assign and remove permissions for a custom role."""
    client.force_login(system_admin)
    role = system_user.user_roles.first().role

    response = client.post(
        reverse("accounts:role_permission_update", kwargs={"role_id": role.id}),
        {"permissions": ["audit.view", "report.view"]},
        follow=True,
    )

    assert response.status_code == 200
    assert system_user.has_permission("audit.view")
    assert system_user.has_permission("report.view")
    assert AuditLog.objects.filter(action="ROLE_PERMISSIONS_UPDATED", entity_id=role.id).exists()


def test_m2_bt_016_system_user_cannot_configure_roles(client, seed_data, system_user):
    """M2-BT-016: System User cannot access role management."""
    client.force_login(system_user)
    response = client.get(reverse("accounts:role_list"))
    assert response.status_code == 403


def test_m2_bt_017_failed_login_throttling(client, seed_data):
    """M2-BT-017: Repeated failed login attempts are throttled."""
    cache.clear()
    for _ in range(5):
        response = client.post(reverse("accounts:login"), {
            "email": "unknown@tcihlc.org",
            "password": "WrongPassword123!",
        })
        assert response.status_code == 401

    response = client.post(reverse("accounts:login"), {
        "email": "unknown@tcihlc.org",
        "password": "WrongPassword123!",
    })
    assert response.status_code == 401
    assert "Too many failed login attempts" in response.content.decode()
    cache.clear()


def test_m2_bt_018_external_login_redirect_is_rejected(client, seed_data, system_admin):
    """M2-BT-018: External post-login redirects are not accepted."""
    response = client.post(
        reverse("accounts:login"),
        {
            "email": settings.SYSTEM_ADMIN_EMAIL,
            "password": settings.SYSTEM_ADMIN_INITIAL_PASSWORD,
            "next": "https://example.com/phishing",
        },
    )
    assert response.status_code == 302
    assert response.url == reverse("core:dashboard")


def test_m2_bt_019_user_creation_exposes_only_custom_roles(client, seed_data, system_admin):
    """M2-BT-019: Create User must not expose protected system roles."""
    role = Role.objects.create(name="Finance Officer", description="Handles finance operations")
    client.force_login(system_admin)

    response = client.get(reverse("accounts:user_create"))
    assert response.status_code == 200
    content = response.content.decode()

    assert "Finance Officer" in content
    assert [r.name for r in response.context["roles"]] == ["Finance Officer"]
    assert "System Administrator" not in [r.name for r in response.context["roles"]]
    assert "System User" not in [r.name for r in response.context["roles"]]


def test_m2_bt_020_role_page_shows_permissions_for_custom_roles(client, seed_data, system_admin):
    """M2-BT-020: System Administrator can see and configure permissions under custom roles."""
    role = Role.objects.create(name="Finance Officer", description="Handles finance operations")
    permission = Permission.objects.get(code="contribution.create")
    RolePermission.objects.create(role=role, permission=permission, assigned_by=system_admin)

    client.force_login(system_admin)
    response = client.get(reverse("accounts:role_list"))
    assert response.status_code == 200
    content = response.content.decode()

    assert "Finance Officer" in content
    assert "Create Contribution" in content
    assert [r.name for r in response.context["roles"]] == ["Finance Officer"]
    assert "System Administrator" not in [r.name for r in response.context["roles"]]
    assert "System User" not in [r.name for r in response.context["roles"]]
