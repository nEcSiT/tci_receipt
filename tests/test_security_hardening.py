import uuid
import pytest
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.test import Client

from apps.accounts.models import Role, User, UserRole, Permission, RolePermission, AdminRecoverySession
from apps.accounts.services import AdminRecoveryService
from apps.contributions.models import ContributionType
from apps.members.models import Member, MemberStatus, TemporaryContributor, TemporaryContributorStatus
from apps.notifications.services import NotificationTemplateService
from apps.receipts.models import Receipt
from apps.receipts.services import ReceiptService
from apps.requisitions.uploads import validate_upload
from django.core.files.uploadedfile import SimpleUploadedFile


@pytest.fixture(autouse=True)
def clear_test_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def unprivileged_user(db):
    user = User.objects.create_user(
        email="unprivileged@tcihlc.org",
        password="ValidPassword123!",
        first_name="Plain",
        last_name="User",
    )
    role = Role.objects.create(name="Guest Staff", description="No permissions")
    UserRole.objects.create(user=user, role=role)
    return user


def test_member_search_api_unauthorized_access_blocked(seed_data, unprivileged_user, system_user):
    """Member search JSON endpoint rejects unauthenticated and unauthorized users."""
    client = Client()

    # Anonymous user -> 401
    resp = client.get("/contributions/members/search/?q=Ni")
    assert resp.status_code == 401

    # Authenticated user without contribution permissions -> 403
    client.force_login(unprivileged_user)
    resp = client.get("/contributions/members/search/?q=Ni")
    assert resp.status_code == 403

    # Authenticated user with contribution.create -> 200
    client.force_login(system_user)
    resp = client.get("/contributions/members/search/?q=Ni")
    assert resp.status_code == 200


def test_temporary_contributor_action_unauthorized_blocked(seed_data, unprivileged_user):
    """Unauthorized users cannot POST to TemporaryContributorActionView."""
    tc = TemporaryContributor.objects.create(
        phone_number="0249998888",
        status=TemporaryContributorStatus.UNVERIFIED,
    )
    client = Client()
    client.force_login(unprivileged_user)

    resp = client.post(f"/members/temporary/{tc.id}/action/", {"action": "under_review"})
    assert resp.status_code == 403


def test_template_format_spec_and_attribute_injection_blocked(seed_data):
    """Format specifiers, conversion flags, and attribute access in templates are rejected."""
    # Format specifier (DoS / memory exhaustion vector)
    with pytest.raises(ValidationError) as exc1:
        NotificationTemplateService.validate_template_body(
            "Dear {first_name:1000000000s}, thank you for your contribution!"
        )
    assert "Format specifiers and conversion modifiers are not allowed" in str(exc1.value)

    # Conversion flag
    with pytest.raises(ValidationError) as exc2:
        NotificationTemplateService.validate_template_body(
            "Dear {first_name!r}, thank you for your contribution!"
        )
    assert "Format specifiers and conversion modifiers are not allowed" in str(exc2.value)

    # Attribute access
    with pytest.raises(ValidationError) as exc3:
        NotificationTemplateService.validate_template_body(
            "Dear {first_name.__class__}, thank you for your contribution!"
        )
    assert "Attribute and index lookups are not allowed" in str(exc3.value)


def test_admin_recovery_verify_rate_limiting_and_lockout(seed_data, system_admin):
    """Recovery session locks after 5 invalid OTP attempts."""
    session, _, _, _ = AdminRecoveryService.initiate_recovery(system_admin.email)
    client = Client()
    session_data = client.session
    session_data["recovery_session_token"] = session.session_token
    session_data.save()

    # 4 invalid attempts
    for _ in range(4):
        resp = client.post("/recovery/verify/", {"step": "email", "otp": "000000"})
        assert resp.status_code == 400

    # 5th attempt triggers lockout
    resp = client.post("/recovery/verify/", {"step": "email", "otp": "000000"})
    assert resp.status_code == 429
    assert "locked for security" in resp.content.decode()

    # Session is marked completed/locked in database
    session.refresh_from_db()
    assert session.is_completed is True


def test_password_reset_rate_limiting(seed_data):
    """Password reset request rate-limiting blocks repeated spamming."""
    client = Client()
    for _ in range(10):
        resp = client.post("/password-reset/", {"email": "user@tcihlc.org"})
        assert resp.status_code == 200

    # 11th attempt is rate-limited
    resp = client.post("/password-reset/", {"email": "user@tcihlc.org"})
    assert resp.status_code == 429
    assert "Too many password reset requests" in resp.content.decode()


def test_receipt_access_rate_limiting(seed_data, system_admin):
    """Receipt access portal rate limits excessive requests."""
    from apps.contributions.models import Contribution, EntryMethod, PaymentMode
    c_type = ContributionType.objects.get(name="Tithe")
    member = Member.objects.create(
        first_name="Rate",
        last_name="LimitTest",
        member_number="MEM-RL001",
        status=MemberStatus.ACTIVE,
    )
    contrib = Contribution.objects.create(
        contribution_type=c_type,
        amount=100,
        currency="GHS",
        payment_mode=PaymentMode.CASH,
        entry_method=EntryMethod.MANUAL,
        recorded_by=system_admin,
        member=member,
    )
    receipt = Receipt.objects.create(
        contribution=contrib,
        generated_by_user=system_admin,
        pdf_storage_key="receipts/2026/11/sample.pdf",
    )
    token = ReceiptService.generate_secure_access_token(receipt)

    client = Client()
    # Populate cache up to the threshold
    cache.set("receipt-access:127.0.0.1", 60, 300)

    resp = client.get(f"/receipts/access/{token}/")
    assert resp.status_code == 429
    assert "Too many receipt access attempts" in resp.content.decode()
