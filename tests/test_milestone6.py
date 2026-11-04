import datetime
import hashlib
import hmac
import json
from decimal import Decimal
import pytest
from django.core.exceptions import ValidationError
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Role, User, UserRole
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
from apps.contributions.providers.base import PaymentResult, PaymentStatus
from apps.contributions.providers.factory import get_payment_provider
from apps.contributions.providers.mock import MockPaymentProvider
from apps.contributions.services import AutomaticPaymentService, UssdService
from apps.members.models import Member, MemberPhoneNumber, MemberStatus, TemporaryContributor
from apps.notifications.models import (
    ManualAction,
    ManualActionPriority,
    ManualActionStatus,
    ManualActionType,
    Notification,
    NotificationAttempt,
    NotificationStatus,
    NotificationTemplate,
    NotificationType,
)
from apps.notifications.providers.factory import get_sms_provider, set_sms_provider_instance
from apps.notifications.providers.mock import MockSmsProvider
from apps.notifications.services import (
    NotificationRetryService,
    NotificationService,
    NotificationTemplateService,
)
from apps.receipts.models import Receipt, ReceiptGenerationAttempt, ReceiptStatus
from apps.receipts.services import ReceiptRetryService, ReceiptService


# =========================================================================
# FIXTURES
# =========================================================================

@pytest.fixture(autouse=True)
def setup_mock_sms():
    mock_sms = MockSmsProvider()
    set_sms_provider_instance(mock_sms)
    yield mock_sms
    mock_sms.clear()
    set_sms_provider_instance(None)


@pytest.fixture
def tithe_type(seed_data):
    return ContributionType.objects.get(name="Tithe")


@pytest.fixture
def thanksgiving_type(seed_data):
    return ContributionType.objects.get(name="Thanksgiving")


@pytest.fixture
def building_type(seed_data):
    return ContributionType.objects.get(name="Building Project")


@pytest.fixture
def partners_type(seed_data):
    return ContributionType.objects.get(name="Higher Life Partners")


@pytest.fixture
def other_type(seed_data):
    return ContributionType.objects.get(name="Other")


@pytest.fixture
def member_with_phones(db):
    member = Member.objects.create(
        first_name="Nicholas",
        last_name="Appiah",
        email="nicholas@tcihlc.org",
        status=MemberStatus.ACTIVE,
    )
    # Primary MTN phone
    MemberPhoneNumber.objects.create(
        member=member,
        phone_number="0241234567",
        is_primary=True,
        is_active=True,
    )
    # Secondary Telecel phone
    MemberPhoneNumber.objects.create(
        member=member,
        phone_number="0209876543",
        is_primary=False,
        is_active=True,
    )
    return member


# =========================================================================
# TESTS: MILESTONE 6 AUTOMATIC PAYMENTS & NOTIFICATIONS
# =========================================================================

def test_m6_bt_001_successful_automatic_payment_end_to_end(seed_data, member_with_phones, tithe_type, setup_mock_sms):
    """
    6.1, 6.7, 6.13, 6.14: Inbound successful automatic payment records transaction,
    strictly identifies member by phone, creates contribution, generates receipt,
    and sends thank-you SMS with secure PDF link.
    """
    result = AutomaticPaymentService.process_payment_confirmation(
        provider_reference="MOMO-TX-00101",
        transaction_phone="0241234567",
        amount=Decimal("250.00"),
        contribution_type_name="Tithe",
        status=PaymentStatus.SUCCESSFUL,
        provider="MTN_MOMO",
    )

    assert result["status"] == "SUCCESSFUL"

    # Verify PaymentTransaction
    payment_tx = result["payment_transaction"]
    assert payment_tx.status == PaymentTransactionStatus.SUCCESSFUL
    assert payment_tx.amount == Decimal("250.00")
    assert payment_tx.provider_reference == "MOMO-TX-00101"
    assert payment_tx.confirmed_at is not None

    # Verify Contribution
    contribution = result["contribution"]
    assert contribution is not None
    assert contribution.member == member_with_phones
    assert contribution.temporary_contributor is None
    assert contribution.amount == Decimal("250.00")
    assert contribution.contribution_type == tithe_type
    assert contribution.entry_method == EntryMethod.AUTOMATIC
    assert contribution.status == ContributionStatus.CONFIRMED

    # Verify Receipt (M5 specifications)
    receipt = result["receipt"]
    assert receipt is not None
    assert receipt.receipt_number.startswith("HLC-")
    assert len(receipt.receipt_number) == 11
    assert receipt.generated_by_system is True
    assert receipt.contributor_name == "Nicholas Appiah"

    # Verify Notification
    notification = result["notification"]
    assert notification is not None
    assert notification.status == NotificationStatus.DELIVERED
    assert notification.recipient_phone == "0241234567"
    assert "Nicholas" in notification.message
    assert "250.00" in notification.message
    assert receipt.receipt_number in notification.message
    assert "/receipts/access/" in notification.message

    # Verify SMS was dispatched via provider
    assert len(setup_mock_sms.sent_messages) == 1
    assert setup_mock_sms.sent_messages[0]["recipient_phone"] == "0241234567"


def test_m6_bt_002_ussd_welcome_and_contribution_type_flow(seed_data):
    """
    6.1 & 6.2: Initial USSD prompt strictly begins with 'Welcome to TCI HLC Givings'
    and supports step-by-step selection of Tithe, Thanksgiving, Partners, Building Project.
    """
    phone = "0241234567"
    session_id = "ussd-sess-1"

    # Step 0: Initial Dial
    res0 = UssdService.handle_request(session_id=session_id, phone_number=phone, text="")
    assert res0["is_terminal"] is False
    assert res0["message"].startswith("Welcome to TCI HLC Givings")
    assert "1. Tithe" in res0["message"]
    assert "2. Thanksgiving" in res0["message"]
    assert "3. Higher Life Partners" in res0["message"]
    assert "4. Building Project" in res0["message"]
    assert "5. Other" in res0["message"]

    # Step 1: Select Tithe (1)
    res1 = UssdService.handle_request(session_id=session_id, phone_number=phone, text="1")
    assert res1["is_terminal"] is False
    assert "Enter amount for Tithe (GHS):" in res1["message"]

    # Step 2: Enter Amount (100)
    res2 = UssdService.handle_request(session_id=session_id, phone_number=phone, text="1*100")
    assert res2["is_terminal"] is False
    assert "Authorize payment of GHS 100.00 for Tithe?" in res2["message"]
    assert "1. Confirm" in res2["message"]
    assert "2. Cancel" in res2["message"]

    # Step 3: Confirm (1)
    res3 = UssdService.handle_request(session_id=session_id, phone_number=phone, text="1*100*1")
    assert res3["is_terminal"] is True
    assert "Payment prompt sent to 0241234567" in res3["message"]
    assert res3["payment_result"] is not None
    assert res3["payment_result"]["status"] == "SUCCESSFUL"
    assert res3["payment_result"]["contribution"].amount == Decimal("100.00")


def test_m6_bt_003_ussd_other_contribution_requires_description(seed_data):
    """
    6.2: If contributor selects 'Other', system must request a description of the contribution.
    """
    phone = "0245556677"
    session_id = "ussd-sess-other"

    # Step 1: Select Other (5)
    res1 = UssdService.handle_request(session_id=session_id, phone_number=phone, text="5")
    assert res1["is_terminal"] is False
    assert "Enter description for your Other contribution:" in res1["message"]

    # Step 2: Enter description
    res2 = UssdService.handle_request(session_id=session_id, phone_number=phone, text="5*Youth Camp Support")
    assert res2["is_terminal"] is False
    assert "Enter amount for Other (Youth Camp Support) in GHS:" in res2["message"]

    # Step 3: Enter amount
    res3 = UssdService.handle_request(session_id=session_id, phone_number=phone, text="5*Youth Camp Support*300")
    assert res3["is_terminal"] is False
    assert "Authorize payment of GHS 300.00 for Other (Youth Camp Support)?" in res3["message"]

    # Step 4: Confirm
    res4 = UssdService.handle_request(session_id=session_id, phone_number=phone, text="5*Youth Camp Support*300*1")
    assert res4["is_terminal"] is True
    assert res4["payment_result"] is not None
    contrib = res4["payment_result"]["contribution"]
    assert contrib.contribution_type.name == "Other"
    assert contrib.custom_type_description == "Youth Camp Support"
    assert contrib.amount == Decimal("300.00")


def test_m6_bt_004_ussd_cancel_flow(seed_data):
    """
    6.1 & 6.9: Selecting cancel terminates the session gracefully without financial side effects.
    """
    res = UssdService.handle_request(session_id="sess-cancel", phone_number="0241112233", text="2*50*2")
    assert res["is_terminal"] is True
    assert "Giving cancelled" in res["message"]
    assert res["payment_result"] is None
    assert Contribution.objects.filter(amount=Decimal("50.00")).count() == 0


def test_m6_bt_005_provider_confirmation_failed_payment(seed_data):
    """
    6.8: Failed payment creates no contribution, no receipt, no thank-you,
    and retains failure code and reason for operational review.
    """
    result = AutomaticPaymentService.process_payment_confirmation(
        provider_reference="MOMO-FAIL-888",
        transaction_phone="0249998877",
        amount=Decimal("150.00"),
        contribution_type_name="Tithe",
        status=PaymentStatus.FAILED,
        failure_code="ERR_INSUFFICIENT_FUNDS",
        failure_reason="Subscriber account has insufficient mobile money balance.",
    )

    assert result["status"] == "FAILED"
    assert result["contribution"] is None
    assert result["receipt"] is None
    assert result["notification"] is None

    # Verify transaction record
    tx = result["payment_transaction"]
    assert tx.status == PaymentTransactionStatus.FAILED
    assert tx.failure_code == "ERR_INSUFFICIENT_FUNDS"
    assert "insufficient" in tx.failure_reason

    # Ensure no contribution or receipt exist
    assert Contribution.objects.filter(reference_number="MOMO-FAIL-888").count() == 0
    assert Receipt.objects.filter(contribution__reference_number="MOMO-FAIL-888").count() == 0

    # Ensure audit log was emitted
    audit = AuditLog.objects.filter(action="PAYMENT_TRANSACTION_FAILED", entity_id=tx.id).first()
    assert audit is not None


def test_m6_bt_006_provider_confirmation_cancelled_payment(seed_data):
    """
    6.9: Cancelled payment retains cancellation info in PaymentTransaction
    and does not create contribution, receipt, or thank you.
    """
    result = AutomaticPaymentService.process_payment_confirmation(
        provider_reference="MOMO-CANCEL-999",
        transaction_phone="0249998877",
        amount=Decimal("100.00"),
        contribution_type_name="Thanksgiving",
        status=PaymentStatus.CANCELLED,
        failure_code="USER_CANCELLED",
        failure_reason="Declined on handset prompt.",
    )

    assert result["status"] == "CANCELLED"
    assert result["contribution"] is None
    assert result["receipt"] is None
    tx = result["payment_transaction"]
    assert tx.status == PaymentTransactionStatus.CANCELLED


def test_m6_bt_007_delayed_pending_payment_workflow(seed_data, member_with_phones):
    """
    6.10: Delayed payment stays pending without receipt or thank-you.
    Once subsequent confirmation succeeds, the flow resumes normally.
    """
    ref = "MOMO-DELAY-001"

    # 1. Delayed initial state
    res_pending = AutomaticPaymentService.process_payment_confirmation(
        provider_reference=ref,
        transaction_phone="0241234567",
        amount=Decimal("500.00"),
        contribution_type_name="Building Project",
        status=PaymentStatus.PENDING,
    )
    assert res_pending["status"] == "PENDING"
    assert res_pending["contribution"] is None
    assert res_pending["receipt"] is None

    tx = PaymentTransaction.objects.get(provider_reference=ref)
    assert tx.status == PaymentTransactionStatus.PENDING

    # 2. Provider confirmation succeeds later
    res_confirmed = AutomaticPaymentService.process_payment_confirmation(
        provider_reference=ref,
        transaction_phone="0241234567",
        amount=Decimal("500.00"),
        contribution_type_name="Building Project",
        status=PaymentStatus.SUCCESSFUL,
    )
    assert res_confirmed["status"] == "SUCCESSFUL"
    assert res_confirmed["contribution"] is not None
    assert res_confirmed["receipt"] is not None
    assert res_confirmed["notification"] is not None


def test_m6_bt_008_phone_identification_multiple_member_phones(seed_data, member_with_phones):
    """
    6.4 & 6.5: Member identification is strictly based on the transaction phone number.
    A member may have multiple phone numbers (MTN, Telecel), each resolving to the same member.
    """
    # Payment via secondary Telecel phone
    result = AutomaticPaymentService.process_payment_confirmation(
        provider_reference="MOMO-TELECEL-01",
        transaction_phone="0209876543",
        amount=Decimal("120.00"),
        contribution_type_name="Tithe",
        status=PaymentStatus.SUCCESSFUL,
        provider="TELECEL_CASH",
        provider_name="Telecel Cash",
    )

    assert result["status"] == "SUCCESSFUL"
    assert result["contribution"].member == member_with_phones
    assert result["contribution"].temporary_contributor is None
    assert "Nicholas" in result["notification"].message


def test_m6_bt_009_unknown_phone_creates_temporary_contributor(seed_data):
    """
    6.6: If transaction phone is unknown, system creates a temporary contributor.
    The system never guesses which existing member the contributor is.
    """
    unknown_phone = "0559990011"
    result = AutomaticPaymentService.process_payment_confirmation(
        provider_reference="MOMO-UNK-77",
        transaction_phone=unknown_phone,
        amount=Decimal("80.00"),
        contribution_type_name="Thanksgiving",
        status=PaymentStatus.SUCCESSFUL,
        provider_name="Guest Contributor",
    )

    assert result["status"] == "SUCCESSFUL"
    contrib = result["contribution"]
    assert contrib.member is None
    assert contrib.temporary_contributor is not None
    assert contrib.temporary_contributor.phone_number == unknown_phone
    assert result["receipt"] is not None
    assert result["notification"] is not None


def test_m6_bt_010_exact_duplicate_detection(seed_data, member_with_phones):
    """
    6.11: Duplicate detection using Provider Reference + Contribution Type.
    Same provider reference and same contribution type treated as duplicate.
    """
    ref = "MOMO-DUP-100"
    # First payment
    res1 = AutomaticPaymentService.process_payment_confirmation(
        provider_reference=ref,
        transaction_phone="0241234567",
        amount=Decimal("75.00"),
        contribution_type_name="Tithe",
        status=PaymentStatus.SUCCESSFUL,
    )
    assert res1["status"] == "SUCCESSFUL"
    original_contrib_id = res1["contribution"].id

    # Second payment with exact same ref and type
    res2 = AutomaticPaymentService.process_payment_confirmation(
        provider_reference=ref,
        transaction_phone="0241234567",
        amount=Decimal("75.00"),
        contribution_type_name="Tithe",
        status=PaymentStatus.SUCCESSFUL,
    )
    assert res2["status"] == "DUPLICATE"
    assert res2["contribution"].id == original_contrib_id
    assert Contribution.objects.filter(reference_number=ref).count() == 1


def test_m6_bt_011_duplicate_reference_with_different_contribution_type_flagged(seed_data, member_with_phones):
    """
    6.11: If same provider reference is received with a DIFFERENT contribution type,
    it must NOT be accepted as normal contribution. Flagged for review / manual action.
    """
    ref = "MOMO-CONFLICT-200"
    # First payment with Tithe
    res1 = AutomaticPaymentService.process_payment_confirmation(
        provider_reference=ref,
        transaction_phone="0241234567",
        amount=Decimal("150.00"),
        contribution_type_name="Tithe",
        status=PaymentStatus.SUCCESSFUL,
    )
    assert res1["status"] == "SUCCESSFUL"

    # Second payment with same ref but different type (Thanksgiving)
    res2 = AutomaticPaymentService.process_payment_confirmation(
        provider_reference=ref,
        transaction_phone="0241234567",
        amount=Decimal("150.00"),
        contribution_type_name="Thanksgiving",
        status=PaymentStatus.SUCCESSFUL,
    )

    assert res2["status"] == "FLAGGED_FOR_REVIEW"
    assert res2["contribution"] is None
    assert res2["manual_action"] is not None
    assert res2["manual_action"].action_type == ManualActionType.DUPLICATE_TRANSACTION_REVIEW
    assert res2["manual_action"].priority == ManualActionPriority.HIGH


def test_m6_bt_012_church_merchant_number_handling(seed_data):
    """
    6.12: If transaction originates from church's merchant number:
    - Do not automatically generate contributor receipt.
    - Log transaction.
    - Notify authorized system user.
    - Place into manual review workflow.
    """
    merchant_phone = "HLC_MERCHANT"
    result = AutomaticPaymentService.process_payment_confirmation(
        provider_reference="MOMO-MERCHANT-505",
        transaction_phone=merchant_phone,
        amount=Decimal("1500.00"),
        contribution_type_name="Tithe",
        status=PaymentStatus.SUCCESSFUL,
    )

    assert result["status"] == "MERCHANT_TRANSACTION"
    assert result["contribution"] is None
    assert result["receipt"] is None
    assert result["manual_action"] is not None
    assert result["manual_action"].action_type == ManualActionType.MERCHANT_TRANSACTION
    assert result["manual_action"].priority == ManualActionPriority.HIGH

    # Verify notification to system was created
    notif = Notification.objects.filter(
        notification_type=NotificationType.MERCHANT_TRANSACTION,
        recipient_phone=merchant_phone,
    ).first()
    assert notif is not None


def test_m6_bt_013_personalized_thank_you_templates_and_variables(seed_data, member_with_phones):
    """
    6.14 & 6.15: System personalizes thank-you messages using contribution-specific
    templates and variables: {first_name}, {amount}, {currency}, {contribution_type}, {receipt_number}, {date}, {secure_link}.
    """
    # Tithe giving
    res_tithe = AutomaticPaymentService.process_payment_confirmation(
        provider_reference="MOMO-T-90",
        transaction_phone="0241234567",
        amount=Decimal("300.00"),
        contribution_type_name="Tithe",
        status=PaymentStatus.SUCCESSFUL,
    )
    msg_tithe = res_tithe["notification"].message
    assert "Nicholas" in msg_tithe
    assert "300.00" in msg_tithe
    assert "Tithe" in msg_tithe or "blessing" in msg_tithe

    # Building Project giving
    res_bldg = AutomaticPaymentService.process_payment_confirmation(
        provider_reference="MOMO-B-91",
        transaction_phone="0241234567",
        amount=Decimal("500.00"),
        contribution_type_name="Building Project",
        status=PaymentStatus.SUCCESSFUL,
    )
    msg_bldg = res_bldg["notification"].message
    assert "Nicholas" in msg_bldg
    assert "500.00" in msg_bldg
    assert "Building Project" in msg_bldg or "House of the Lord" in msg_bldg


def test_m6_bt_014_notification_lifecycle_success(seed_data, member_with_phones):
    """
    6.17 & 6.18: Notification lifecycle moves Pending -> Sending -> Delivered.
    Retains notification ID, channel, attempt count, timestamps, provider reference.
    """
    result = AutomaticPaymentService.process_payment_confirmation(
        provider_reference="MOMO-NOTIF-01",
        transaction_phone="0241234567",
        amount=Decimal("50.00"),
        contribution_type_name="Thanksgiving",
        status=PaymentStatus.SUCCESSFUL,
    )
    notif = result["notification"]
    assert notif.status == NotificationStatus.DELIVERED
    assert notif.channel == "SMS"
    assert notif.attempt_count == 1
    assert notif.sent_at is not None
    assert notif.delivered_at is not None
    assert notif.provider_reference is not None

    # Check NotificationAttempt record
    attempts = notif.attempts.all()
    assert attempts.count() == 1
    assert attempts[0].attempt_number == 1
    assert attempts[0].status == "DELIVERED"


def test_m6_bt_015_notification_retry_and_three_attempt_limit_manual_action(seed_data, setup_mock_sms):
    """
    6.17, 6.19: Failed notifications are retried up to 3 attempts.
    After the 3rd failed attempt, automatic retries stop, status becomes MANUAL_ACTION_REQUIRED,
    and a Manual Action is created.
    """
    setup_mock_sms.set_phone_to_fail("0249999999")

    # Initial send fails (attempt 1)
    notif = NotificationService.create_and_send_notification(
        recipient_phone="0249999999",
        message="Test thank you message",
        notification_type=NotificationType.THANK_YOU,
        related_record_type="Contribution",
        related_record_id=None,
    )
    assert notif.status == NotificationStatus.FAILED
    assert notif.attempt_count == 1
    assert notif.attempts.count() == 1

    # Retry attempt 2 fails
    NotificationRetryService.retry_notification(notif, max_attempts=3)
    notif.refresh_from_db()
    assert notif.status == NotificationStatus.FAILED
    assert notif.attempt_count == 2
    assert notif.attempts.count() == 2

    # Retry attempt 3 fails -> transitions to MANUAL_ACTION_REQUIRED
    NotificationRetryService.retry_notification(notif, max_attempts=3)
    notif.refresh_from_db()
    assert notif.status == NotificationStatus.MANUAL_ACTION_REQUIRED
    assert notif.attempt_count == 3
    assert notif.attempts.count() == 3

    # Verify Manual Action was created
    action = ManualAction.objects.filter(
        related_record_type="Notification",
        related_record_id=notif.id,
        action_type=ManualActionType.THANK_YOU_NOTIFICATION_FAILED,
    ).first()
    assert action is not None
    assert action.priority == ManualActionPriority.MEDIUM
    assert action.status == ManualActionStatus.OPEN
    assert action.attempt_count == 3


def test_m6_bt_016_notification_failure_independence(seed_data, member_with_phones, setup_mock_sms):
    """
    6.21: Notification failure must NEVER reverse the underlying financial event.
    Payment remains successful, contribution confirmed, receipt valid.
    """
    setup_mock_sms.set_phone_to_fail("0241234567")

    result = AutomaticPaymentService.process_payment_confirmation(
        provider_reference="MOMO-FAIL-IND-01",
        transaction_phone="0241234567",
        amount=Decimal("400.00"),
        contribution_type_name="Tithe",
        status=PaymentStatus.SUCCESSFUL,
    )

    # Financial event remains confirmed!
    contrib = result["contribution"]
    assert contrib.status == ContributionStatus.CONFIRMED
    assert contrib.amount == Decimal("400.00")

    # Receipt remains valid and active!
    receipt = result["receipt"]
    assert receipt.status == ReceiptStatus.ACTIVE

    # Only notification was marked as failed
    notif = result["notification"]
    assert notif.status in [NotificationStatus.FAILED, NotificationStatus.MANUAL_ACTION_REQUIRED]


def test_m6_bt_017_receipt_generation_retry_and_manual_action(seed_data, member_with_phones):
    """
    6.20: If automatic receipt generation fails, it retries up to 3 times.
    On 3rd failure, creates Manual Action, but retains the successful contribution!
    """
    result = AutomaticPaymentService.process_payment_confirmation(
        provider_reference="MOMO-RECEIPT-RETRY-01",
        transaction_phone="0241234567",
        amount=Decimal("350.00"),
        contribution_type_name="Building Project",
        status=PaymentStatus.SUCCESSFUL,
        simulate_receipt_failure_count=3,  # Fails all 3 attempts
    )

    assert result["status"] == "SUCCESSFUL"
    contrib = result["contribution"]
    assert contrib.status == ContributionStatus.CONFIRMED

    # Receipt is None because generation failed 3 times
    assert result["receipt"] is None

    # Check ReceiptGenerationAttempt logs
    attempts = ReceiptGenerationAttempt.objects.filter(contribution=contrib)
    assert attempts.count() == 3
    for att in attempts:
        assert att.status == "FAILED"

    # Manual Action created for receipt failure
    action = ManualAction.objects.filter(
        related_record_type="Contribution",
        related_record_id=contrib.id,
        action_type=ManualActionType.RECEIPT_GENERATION_FAILED,
    ).first()
    assert action is not None
    assert action.priority == ManualActionPriority.HIGH
    assert action.status == ManualActionStatus.OPEN


def test_m6_bt_018_provider_abstraction_swappable(seed_data):
    """
    6.22: PaymentProvider and SmsProvider abstractions are swappable via settings.
    No credentials in source code.
    """
    payment_provider = get_payment_provider()
    assert isinstance(payment_provider, MockPaymentProvider)
    assert payment_provider.provider_code == "MTN_MOMO"

    sms_provider = get_sms_provider()
    assert isinstance(sms_provider, MockSmsProvider)


@override_settings(PAYMENT_WEBHOOK_SECRET="test-webhook-secret")
def test_m6_bt_019_api_and_ussd_gateway_endpoints(client, seed_data, member_with_phones):
    """
    Tests HTTP endpoints:
    1. /contributions/webhook/payment/ (Payment Provider webhook)
    2. /contributions/ussd/ (Telecom USSD gateway)
    """
    # 1. Webhook endpoint
    webhook_url = reverse("contributions:payment_webhook")
    webhook_payload = {
        "reference": "WEBHOOK-REF-001",
        "phone": "0241234567",
        "amount": "175.00",
        "type": "Tithe",
        "status": "SUCCESSFUL",
    }
    raw_body = json.dumps(webhook_payload).encode("utf-8")
    signature = hmac.new(b"test-webhook-secret", raw_body, hashlib.sha256).hexdigest()
    response = client.post(webhook_url, data=raw_body, content_type="application/json", HTTP_X_WEBHOOK_SIGNATURE=signature)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "SUCCESSFUL"
    assert data["provider_reference"] == "WEBHOOK-REF-001"
    assert data["contribution_number"] is not None
    assert data["receipt_number"] is not None

    unauth = client.post(webhook_url, data=raw_body, content_type="application/json")
    assert unauth.status_code == 401

    # 2. USSD endpoint
    ussd_url = reverse("contributions:ussd_gateway")
    # Initial menu
    ussd_res0 = client.get(ussd_url, {"sessionId": "s1", "phoneNumber": "0241234567", "text": ""})
    assert ussd_res0.status_code == 200
    assert ussd_res0.content.decode().startswith("CON Welcome to TCI HLC Givings")

    # Final confirm step
    ussd_res1 = client.get(ussd_url, {"sessionId": "s1", "phoneNumber": "0241234567", "text": "1*175*1"})
    assert ussd_res1.status_code == 200
    assert ussd_res1.content.decode().startswith("END Payment prompt sent")


def test_m6_bt_020_audit_and_traceability_chain(seed_data, member_with_phones):
    """
    6.23: Complete traceability chain:
    Payment Transaction -> Contribution -> Receipt -> Notification -> NotificationAttempt -> AuditLog
    """
    ref = "MOMO-TRACE-999"
    result = AutomaticPaymentService.process_payment_confirmation(
        provider_reference=ref,
        transaction_phone="0241234567",
        amount=Decimal("220.00"),
        contribution_type_name="Thanksgiving",
        status=PaymentStatus.SUCCESSFUL,
    )

    payment_tx = result["payment_transaction"]
    contrib = result["contribution"]
    receipt = result["receipt"]
    notif = result["notification"]

    # Traceability links
    assert contrib.payment_transaction == payment_tx
    assert receipt.contribution == contrib
    assert notif.related_record_type == "Contribution"
    assert notif.related_record_id == contrib.id

    # Audit records exist for all stages
    assert AuditLog.objects.filter(entity_type="Contribution", entity_id=contrib.id, action="AUTOMATIC_CONTRIBUTION_RECORDED").exists()
    assert AuditLog.objects.filter(entity_type="Receipt", entity_id=receipt.id, action="RECEIPT_GENERATION_SUCCESS").exists()
    assert AuditLog.objects.filter(entity_type="Notification", entity_id=notif.id, action="NOTIFICATION_SENT").exists()


def test_m6_security_storage_path_traversal_blocked(tmp_path):
    from apps.core.services.storage import LocalStorageService
    storage = LocalStorageService(base_dir=tmp_path / "media")
    with pytest.raises(ValueError):
        storage.get("../outside.pdf")
