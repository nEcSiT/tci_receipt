import decimal
import pytest
from django.conf import settings
from django.core.exceptions import ValidationError, PermissionDenied
from django.core.signing import TimestampSigner
from django.db import connection, transaction
from django.test import Client
from django.utils import timezone

from apps.accounts.models import Role, User
from apps.audit.models import AuditLog
from apps.contributions.models import (
    Contribution,
    ContributionStatus,
    ContributionType,
    EntryMethod,
    PaymentMode,
    PaymentTransaction,
    PaymentTransactionStatus,
)
from apps.contributions.services import ContributorIdentificationService
from apps.members.models import (
    ContributorLinkingRequest,
    ContributorLinkingStatus,
    Member,
    MemberPhoneNumber,
    MemberStatus,
    TemporaryContributor,
    TemporaryContributorStatus,
)
from apps.members.services import MemberService, TemporaryContributorService
from apps.notifications.models import Notification, NotificationType
from apps.receipts.models import Receipt, ReceiptStatus
from apps.receipts.services import ReceiptPdfService, ReceiptService


@pytest.fixture
def test_setup(seed_data, system_admin):
    """Sets up standard roles, users, and contribution types."""
    from apps.accounts.models import Permission, RolePermission, UserRole

    # Create Staff role with member permissions
    staff_role = Role.objects.create(name="Staff Reviewer", description="Staff role")
    for code in ["member.view", "member.create", "member.edit", "contribution.view"]:
        p = Permission.objects.filter(code=code).first()
        if p:
            RolePermission.objects.create(role=staff_role, permission=p)

    staff = User.objects.create_user(
        email="staff_review@tcihlc.org",
        password="StaffPassword123!",
        first_name="Staff",
        last_name="Member",
    )
    UserRole.objects.create(user=staff, role=staff_role)

    # Unauthorized staff with only notification.view
    unauth_role = Role.objects.create(name="Unauth Role", description="Unauth role")
    p_notif = Permission.objects.filter(code="notification.view").first()
    if p_notif:
        RolePermission.objects.create(role=unauth_role, permission=p_notif)
    unauthorized_staff = User.objects.create_user(
        email="unauth_review@tcihlc.org",
        password="UnauthPassword123!",
        first_name="Unauth",
        last_name="User",
    )
    UserRole.objects.create(user=unauthorized_staff, role=unauth_role)

    tithe_type = ContributionType.objects.get_or_create(name="Tithe")[0]
    offering_type = ContributionType.objects.get_or_create(name="Offering")[0]

    return {
        "admin": system_admin,
        "staff": staff,
        "unauthorized_staff": unauthorized_staff,
        "tithe_type": tithe_type,
        "offering_type": offering_type,
    }


@pytest.fixture
def temp_contributor_with_contributions(test_setup):
    """Creates a temporary contributor with automatic contributions and receipts."""
    _, tc = ContributorIdentificationService.identify_contributor(
        phone_number="+233240001234",
        provider="MTN_MOMO",
        provider_name="Kofi Provider Payer",
    )
    tc.first_name = "Kofi"
    tc.last_name = "Temp"
    tc.save()

    tx = PaymentTransaction.objects.create(
        provider="MTN_MOMO",
        provider_reference="TX-REVIEW-001",
        transaction_phone="+233240001234",
        amount=decimal.Decimal("300.00"),
        status=PaymentTransactionStatus.SUCCESSFUL,
        provider_name="Kofi Provider Payer",
    )
    contrib = Contribution.objects.create(
        temporary_contributor=tc,
        contribution_type=test_setup["tithe_type"],
        amount=decimal.Decimal("300.00"),
        currency="GHS",
        payment_mode=PaymentMode.MOBILE_MONEY,
        entry_method=EntryMethod.AUTOMATIC,
        payment_transaction=tx,
        reference_number="REF-TX-001",
        status=ContributionStatus.CONFIRMED,
    )
    receipt = ReceiptService.generate_receipt(contrib, is_system=True)

    return {
        "tc": tc,
        "contrib": contrib,
        "receipt": receipt,
        "tx": tx,
    }


# ==============================================================================
# 1. REVIEW CONTRIBUTOR SCREEN TESTS
# ==============================================================================

def test_01_review_contributor_screen_displays_unmatched_explanation_and_actions(client, test_setup, temp_contributor_with_contributions):
    """
    Review contributor screen:
    - Accessible from temporary contributors list and contribution detail
    - Displays supplied name, transaction phone, contribution history, and Unverified status
    - Explains: 'This contribution has not yet been matched to a church member.'
    - Displays two distinct actions: 'Register as a new member' and 'Link to an existing member'
    """
    client.force_login(test_setup["staff"])
    tc = temp_contributor_with_contributions["tc"]
    receipt = temp_contributor_with_contributions["receipt"]

    response = client.get(f"/members/temporary/{tc.id}/review/")
    assert response.status_code == 200
    content = response.content.decode("utf-8")

    # Business rule explanation
    assert "This contribution has not yet been matched to a church member." in content
    assert "Unverified" in content

    # Profile & Phone
    assert tc.phone_number in content
    assert "Kofi Provider Payer" in content

    # Actions available
    assert f"/members/temporary/{tc.id}/register/" in content
    assert f"/members/temporary/{tc.id}/link/" in content

    # Contribution history and receipt actions
    assert receipt.receipt_number in content
    assert f"/receipts/{receipt.id}/" in content
    assert f"/receipts/{receipt.id}/pdf/?download=1" in content


def test_02_contribution_detail_links_to_review_contributor_screen(client, test_setup, temp_contributor_with_contributions):
    """
    Contribution detail screen contains link to Review Contributor screen for unmatched contributors.
    """
    client.force_login(test_setup["staff"])
    contrib = temp_contributor_with_contributions["contrib"]
    tc = temp_contributor_with_contributions["tc"]

    response = client.get(f"/contributions/{contrib.id}/")
    assert response.status_code == 200
    content = response.content.decode("utf-8")

    assert f"/members/temporary/{tc.id}/review/" in content
    assert "Review Contributor" in content
    assert "Not yet linked to a church member." in content


# ==============================================================================
# 2. TEMPORARY CONTRIBUTOR REGISTRATION TESTS
# ==============================================================================

def test_03_temporary_contributor_registration_success(client, test_setup, temp_contributor_with_contributions):
    """
    Register as a new member:
    - Proper member form prefilled with available information
    - Atomic transaction reassigns contributions and creates member
    - Receipt numbers, IDs, amounts, payment references are strictly preserved
    - No duplicate thank-you SMS is sent
    - Temporary contributor status becomes CONVERTED with converted_member FK
    - Finishes with 'Member registered successfully'
    """
    client.force_login(test_setup["staff"])
    tc = temp_contributor_with_contributions["tc"]
    contrib = temp_contributor_with_contributions["contrib"]
    receipt = temp_contributor_with_contributions["receipt"]
    original_receipt_number = receipt.receipt_number
    original_contrib_id = contrib.contribution_number

    # GET form verifies prefill
    get_resp = client.get(f"/members/temporary/{tc.id}/register/")
    assert get_resp.status_code == 200
    assert "Previous contributions" in get_resp.content.decode("utf-8")
    assert "Kofi Provider Payer" in get_resp.content.decode("utf-8")

    # Count notifications before
    notif_count_before = Notification.objects.count()

    # POST registration
    post_resp = client.post(
        f"/members/temporary/{tc.id}/register/",
        {
            "first_name": "Kofi",
            "middle_name": "Kwame",
            "last_name": "Mensah",
            "email": "kofi.mensah@example.com",
            "ministry": "Men of Faith",
            "team": "Choir",
            "department": "Worship",
        },
        follow=True,
    )
    assert post_resp.status_code == 200
    assert "Member registered successfully" in post_resp.content.decode("utf-8")

    # Verify member created
    new_member = Member.objects.get(email="kofi.mensah@example.com")
    assert new_member.full_name == "Kofi Kwame Mensah"
    assert new_member.ministry == "Men of Faith"

    # Verify primary active phone
    phone_rec = new_member.phone_numbers.filter(phone_number=tc.phone_number, is_active=True).first()
    assert phone_rec is not None
    assert phone_rec.is_primary is True

    # Verify contribution reassigned
    contrib.refresh_from_db()
    assert contrib.member == new_member
    assert contrib.temporary_contributor is None
    assert contrib.contribution_number == original_contrib_id

    # Verify receipt preserved and contributor name resolved to new member
    receipt.refresh_from_db()
    assert receipt.receipt_number == original_receipt_number
    assert receipt.contributor_name == "Kofi Kwame Mensah"

    # Verify temporary contributor state
    tc.refresh_from_db()
    assert tc.status == TemporaryContributorStatus.CONVERTED
    assert tc.converted_member == new_member

    # Verify NO extra SMS notification sent
    assert Notification.objects.count() == notif_count_before

    # Verify audit log recorded
    assert AuditLog.objects.filter(
        action="TEMPORARY_CONTRIBUTOR_CONVERTED",
        entity_id=tc.id,
    ).exists()


def test_04_temporary_contributor_registration_phone_conflict(client, test_setup, temp_contributor_with_contributions):
    """
    Check normalized phone numbers for existing membership before creating another member.
    If phone belongs to an active member, rejects with:
    'This phone number is already registered to another member. Please review the existing member.'
    """
    client.force_login(test_setup["staff"])
    tc = temp_contributor_with_contributions["tc"]

    # Create an existing member with the same normalized phone
    existing_member = MemberService.create_member(
        creator=test_setup["admin"],
        first_name="Existing",
        last_name="Member",
        phone_number=tc.phone_number,
    )

    # Attempt to register temporary contributor with same phone
    post_resp = client.post(
        f"/members/temporary/{tc.id}/register/",
        {
            "first_name": "Conflict",
            "last_name": "Person",
        },
    )
    assert post_resp.status_code == 400
    content = post_resp.content.decode("utf-8")
    assert "This phone number is already registered to another member. Please review the existing member." in content

    # Verify no new member created with name Conflict Person
    assert not Member.objects.filter(first_name="Conflict", last_name="Person").exists()
    tc.refresh_from_db()
    assert tc.status == TemporaryContributorStatus.UNVERIFIED


def test_05_temporary_contributor_registration_duplicate_submission_blocked(test_setup, temp_contributor_with_contributions):
    """
    Prevents duplicate registration from repeated clicks or concurrent requests.
    """
    tc = temp_contributor_with_contributions["tc"]
    staff = test_setup["staff"]

    # First conversion succeeds
    m1 = TemporaryContributorService.convert_to_member(
        actor=staff,
        temporary_contributor=tc,
        first_name="Kwame",
        last_name="Appiah",
    )
    assert m1 is not None

    # Second conversion attempt fails
    tc.refresh_from_db()
    with pytest.raises(ValidationError) as exc:
        TemporaryContributorService.convert_to_member(
            actor=staff,
            temporary_contributor=tc,
            first_name="Repeat",
            last_name="Attempt",
        )
    assert "already been registered or linked" in str(exc.value)


def test_06_temporary_contributor_registration_permissions_enforced(client, test_setup, temp_contributor_with_contributions):
    """
    Unauthorized staff cannot register members from temporary contributors.
    """
    client.force_login(test_setup["unauthorized_staff"])
    tc = temp_contributor_with_contributions["tc"]

    resp = client.post(
        f"/members/temporary/{tc.id}/register/",
        {"first_name": "Unauthorized", "last_name": "Try"},
    )
    assert resp.status_code == 403


# ==============================================================================
# 3. CONTRIBUTOR LINKING WORKFLOW TESTS
# ==============================================================================

def test_07_contributor_linking_request_submission_by_staff(client, test_setup, temp_contributor_with_contributions):
    """
    Linking workflow:
    - Ordinary authorized user submits request
    - Contributions are NOT transferred before approval
    - Displays: 'Your request has been sent to the administrator for approval.'
    - Status is PENDING
    """
    client.force_login(test_setup["staff"])
    tc = temp_contributor_with_contributions["tc"]
    contrib = temp_contributor_with_contributions["contrib"]

    target_member = MemberService.create_member(
        creator=test_setup["admin"],
        first_name="Ama",
        last_name="Boateng",
        phone_number="+233501112233",
    )

    # Submit linking request
    resp = client.post(
        f"/members/temporary/{tc.id}/link/",
        {
            "target_member_id": str(target_member.id),
            "reason": "Confirmed during church welfare visitation that Kofi is Ama's son and gave on her behalf.",
            "add_phone": "on",
        },
        follow=True,
    )
    assert resp.status_code == 200
    content = resp.content.decode("utf-8")
    assert "Your request has been sent to the administrator for approval." in content

    # Verify request created with PENDING status
    linking_req = ContributorLinkingRequest.objects.get(temporary_contributor=tc)
    assert linking_req.status == ContributorLinkingStatus.PENDING
    assert linking_req.target_member == target_member
    assert linking_req.requested_by == test_setup["staff"]
    assert linking_req.has_phone_conflict is False

    # CRUCIAL: Contributions NOT transferred before approval!
    contrib.refresh_from_db()
    assert contrib.temporary_contributor == tc
    assert contrib.member is None
    tc.refresh_from_db()
    assert tc.status == TemporaryContributorStatus.UNVERIFIED


def test_08_contributor_linking_phone_conflict_detection(client, test_setup, temp_contributor_with_contributions):
    """
    Flag phone conflicts for authorized resolution; never silently skip them or match people by name alone.
    """
    client.force_login(test_setup["staff"])
    tc = temp_contributor_with_contributions["tc"]

    # Third-party member who holds the contributor's phone
    other_member = MemberService.create_member(
        creator=test_setup["admin"],
        first_name="Third",
        last_name="Party",
        phone_number=tc.phone_number,
    )

    # Intended target member (different person)
    target_member = MemberService.create_member(
        creator=test_setup["admin"],
        first_name="Target",
        last_name="Person",
        phone_number="+233209998877",
    )

    # Submit linking request
    resp = client.post(
        f"/members/temporary/{tc.id}/link/",
        {
            "target_member_id": str(target_member.id),
            "reason": "Contributor stated they recently bought this SIM card from someone else.",
            "add_phone": "on",
        },
        follow=True,
    )
    assert resp.status_code == 200
    content = resp.content.decode("utf-8")
    assert "This phone number is already registered to another member. Please review the existing member." in content

    linking_req = ContributorLinkingRequest.objects.get(temporary_contributor=tc)
    assert linking_req.has_phone_conflict is True
    assert "Third Party" in linking_req.phone_conflict_details


def test_09_system_administrator_approves_linking(client, test_setup, temp_contributor_with_contributions):
    """
    System Administrator approves linking request:
    - Reassigns contributions atomically
    - Preserves receipt numbers, IDs, amounts, references
    - Does NOT send another thank-you SMS
    - Updates contributor status to LINKED
    - Logs audit trail
    """
    tc = temp_contributor_with_contributions["tc"]
    contrib = temp_contributor_with_contributions["contrib"]
    receipt = temp_contributor_with_contributions["receipt"]
    original_receipt_number = receipt.receipt_number
    original_contrib_id = contrib.contribution_number

    target_member = MemberService.create_member(
        creator=test_setup["admin"],
        first_name="Grace",
        last_name="Osei",
        phone_number="+233261234567",
    )

    linking_req = TemporaryContributorService.request_linking(
        actor=test_setup["staff"],
        temporary_contributor=tc,
        target_member=target_member,
        reason="Family contribution confirmation",
        add_phone=True,
    )

    notif_count_before = Notification.objects.count()

    # System Administrator approves
    client.force_login(test_setup["admin"])
    resp = client.post(
        f"/members/temporary/linking-requests/{linking_req.id}/review/",
        {
            "action": "approve",
            "remarks": "Verified with both parties.",
        },
        follow=True,
    )
    assert resp.status_code == 200

    linking_req.refresh_from_db()
    assert linking_req.status == ContributorLinkingStatus.APPROVED
    assert linking_req.reviewed_by == test_setup["admin"]
    assert linking_req.review_remarks == "Verified with both parties."

    # Contributions transferred
    contrib.refresh_from_db()
    assert contrib.member == target_member
    assert contrib.temporary_contributor is None
    assert contrib.contribution_number == original_contrib_id

    # Receipt preserved
    receipt.refresh_from_db()
    assert receipt.receipt_number == original_receipt_number
    assert receipt.contributor_name == "Grace Osei"

    # Temporary contributor updated
    tc.refresh_from_db()
    assert tc.status == TemporaryContributorStatus.LINKED
    assert tc.converted_member == target_member

    # Phone added to target member
    assert target_member.phone_numbers.filter(phone_number=tc.phone_number, is_active=True).exists()

    # NO duplicate thank you SMS sent
    assert Notification.objects.count() == notif_count_before

    # Audit log recorded
    assert AuditLog.objects.filter(
        action="CONTRIBUTOR_LINKING_APPROVED",
        entity_id=linking_req.id,
    ).exists()


def test_10_system_administrator_rejects_linking(client, test_setup, temp_contributor_with_contributions):
    """
    System Administrator rejects linking request:
    - Contributions remain with temporary contributor
    - Contributor remains UNVERIFIED
    - Audit trail recorded
    """
    tc = temp_contributor_with_contributions["tc"]
    contrib = temp_contributor_with_contributions["contrib"]

    target_member = MemberService.create_member(
        creator=test_setup["admin"],
        first_name="Daniel",
        last_name="Arthur",
        phone_number="+233271112233",
    )

    linking_req = TemporaryContributorService.request_linking(
        actor=test_setup["staff"],
        temporary_contributor=tc,
        target_member=target_member,
        reason="Suspected match based on name",
    )

    client.force_login(test_setup["admin"])
    resp = client.post(
        f"/members/temporary/linking-requests/{linking_req.id}/review/",
        {
            "action": "reject",
            "remarks": "Name match insufficient; phone owner confirmed not related.",
        },
        follow=True,
    )
    assert resp.status_code == 200

    linking_req.refresh_from_db()
    assert linking_req.status == ContributorLinkingStatus.REJECTED
    assert linking_req.reviewed_by == test_setup["admin"]

    # Contributions NOT transferred
    contrib.refresh_from_db()
    assert contrib.temporary_contributor == tc
    assert contrib.member is None
    tc.refresh_from_db()
    assert tc.status == TemporaryContributorStatus.UNVERIFIED

    # Audit log recorded
    assert AuditLog.objects.filter(
        action="CONTRIBUTOR_LINKING_REJECTED",
        entity_id=linking_req.id,
    ).exists()


def test_11_ordinary_user_cannot_approve_linking_request(client, test_setup, temp_contributor_with_contributions):
    """
    Only the System Administrator can approve or reject contributor linking requests.
    Ordinary users receive 403 PermissionDenied.
    """
    tc = temp_contributor_with_contributions["tc"]
    target_member = MemberService.create_member(
        creator=test_setup["admin"],
        first_name="Samuel",
        last_name="Donkor",
        phone_number="+233281112233",
    )
    linking_req = TemporaryContributorService.request_linking(
        actor=test_setup["staff"],
        temporary_contributor=tc,
        target_member=target_member,
        reason="Testing permission boundaries",
    )

    client.force_login(test_setup["staff"])  # Staff is NOT system administrator
    resp = client.post(
        f"/members/temporary/linking-requests/{linking_req.id}/review/",
        {"action": "approve"},
    )
    assert resp.status_code == 403


# ==============================================================================
# 4. RECEIPT VIEWING AND DOWNLOADING TESTS
# ==============================================================================

def test_12_receipt_view_and_download_for_unverified_temporary_contributor(client, test_setup, temp_contributor_with_contributions):
    """
    Ensure temporary/unverified contributor receipts work before member registration:
    - View receipt opens HTML receipt on screen
    - Download PDF returns PDF bytes with Content-Disposition attachment and receipt number filename
    - Never displays contributor phone number
    """
    client.force_login(test_setup["staff"])
    receipt = temp_contributor_with_contributions["receipt"]
    tc = temp_contributor_with_contributions["tc"]

    # 1. View receipt on screen
    view_resp = client.get(f"/receipts/{receipt.id}/")
    assert view_resp.status_code == 200
    content = view_resp.content.decode("utf-8")
    assert receipt.receipt_number in content
    # Contributor phone MUST NOT appear in receipt view
    assert tc.phone_number not in content

    # 2. Download PDF as attachment
    pdf_resp = client.get(f"/receipts/{receipt.id}/pdf/?download=1")
    assert pdf_resp.status_code == 200
    assert pdf_resp["Content-Type"] == "application/pdf"
    assert f'attachment; filename="{receipt.receipt_number}.pdf"' in pdf_resp["Content-Disposition"]
    assert pdf_resp.content.startswith(b"%PDF")


def test_13_public_sms_receipt_access_valid_token(client, temp_contributor_with_contributions):
    """
    A contributor with a valid SMS link must be able to view that receipt and download
    its PDF without a staff account.
    """
    unauthenticated_client = Client()
    receipt = temp_contributor_with_contributions["receipt"]
    tc = temp_contributor_with_contributions["tc"]

    token = ReceiptService.generate_secure_access_token(receipt)

    # 1. View receipt via public access token without logging in
    view_resp = unauthenticated_client.get(f"/receipts/access/{token}/")
    assert view_resp.status_code == 200
    content = view_resp.content.decode("utf-8")
    assert receipt.receipt_number in content
    assert "Official Church Receipt" in content
    assert tc.phone_number not in content  # Privacy invariant

    # 2. Download official PDF via public access token without logging in
    download_resp = unauthenticated_client.get(f"/receipts/access/{token}/?download=1")
    assert download_resp.status_code == 200
    assert download_resp["Content-Type"] == "application/pdf"
    assert f'attachment; filename="{receipt.receipt_number}.pdf"' in download_resp["Content-Disposition"]
    assert download_resp.content.startswith(b"%PDF")


def test_14_public_sms_receipt_access_expired_link(client, temp_contributor_with_contributions, monkeypatch):
    """
    Expired receipt link renders user-friendly message:
    'This receipt link has expired. Please contact the church office for assistance.'
    """
    unauthenticated_client = Client()
    receipt = temp_contributor_with_contributions["receipt"]

    token = ReceiptService.generate_secure_access_token(receipt)

    # Set token max age to -1 to simulate expiration
    monkeypatch.setattr(settings, "RECEIPT_ACCESS_TOKEN_MAX_AGE", -1)

    resp = unauthenticated_client.get(f"/receipts/access/{token}/")
    assert resp.status_code == 404
    content = resp.content.decode("utf-8")
    assert "This receipt link has expired. Please contact the church office for assistance." in content


def test_15_public_sms_receipt_access_tampered_link(client):
    """
    Tampered receipt link renders user-friendly message without raw exceptions:
    'This receipt access link is invalid, expired, or has been revoked.'
    """
    unauthenticated_client = Client()
    resp = unauthenticated_client.get("/receipts/access/tampered-bad-token-12345/")
    assert resp.status_code == 404
    content = resp.content.decode("utf-8")
    assert "This receipt access link is invalid, expired, or has been revoked." in content


def test_16_public_receipt_url_builder_enforces_https_and_configured_domain(temp_contributor_with_contributions, monkeypatch):
    """
    Complete SMS links generated using configured, trusted public application address.
    Enforces HTTPS in production; never sends localhost or relative paths.
    """
    receipt = temp_contributor_with_contributions["receipt"]
    monkeypatch.setattr(settings, "PUBLIC_APP_URL", "https://receipts.tcihlc.org")
    monkeypatch.setattr(settings, "DEBUG", False)

    token = ReceiptService.generate_secure_access_token(receipt)
    url = ReceiptService.build_public_access_url(token)

    assert url.startswith("https://receipts.tcihlc.org/receipts/access/")
    assert "/receipts/access/" in url
    assert "localhost" not in url
    assert not url.startswith("/")


def test_17_staff_links_retain_authorization_notification_access_isolated(client, test_setup, temp_contributor_with_contributions):
    """
    Notification access must not automatically grant access to every receipt.
    Staff with only notification.view cannot access other users' receipts.
    """
    client.force_login(test_setup["unauthorized_staff"])
    receipt = temp_contributor_with_contributions["receipt"]

    resp = client.get(f"/receipts/{receipt.id}/")
    assert resp.status_code == 403


# ==============================================================================
# 5. POSTGRESQL CONCURRENCY TEST
# ==============================================================================

@pytest.mark.django_db(transaction=True)
def test_18_postgresql_concurrent_registration(test_setup):
    """
    PostgreSQL concurrency test:
    Verify that concurrent registration attempts on the same temporary contributor
    lock cleanly with select_for_update, preventing double registration.
    """
    if connection.vendor != "postgresql":
        pytest.skip("Requires PostgreSQL row locking; SQLite cannot validate concurrency across threads.")

    _, tc = ContributorIdentificationService.identify_contributor(
        phone_number="+233249998877",
        provider="MTN_MOMO",
        provider_name="Concurrent Giver",
    )

    import threading
    results = []
    errors = []

    def _convert(first_name):
        try:
            m = TemporaryContributorService.convert_to_member(
                actor=test_setup["admin"],
                temporary_contributor=tc,
                first_name=first_name,
                last_name="Tester",
            )
            results.append(m)
        except Exception as e:
            errors.append(e)

    t1 = threading.Thread(target=_convert, args=("Thread1",))
    t2 = threading.Thread(target=_convert, args=("Thread2",))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    # Exactly one thread succeeds and the other receives a ValidationError
    assert len(results) == 1
    assert len(errors) == 1
    assert any("already been registered or linked" in str(e) for e in errors)
