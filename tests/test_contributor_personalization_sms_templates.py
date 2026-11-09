import uuid
from decimal import Decimal
import pytest
from django.core.exceptions import ValidationError
from django.test import Client

from apps.accounts.models import Role, User, UserRole, Permission, RolePermission
from apps.contributions.models import (
    Contribution,
    ContributionStatus,
    ContributionType,
    EntryMethod,
    PaymentTransactionStatus,
)
from apps.contributions.providers.base import PaymentStatus
from apps.contributions.services import AutomaticPaymentService
from apps.members.models import Member, MemberPhoneNumber, MemberStatus, TemporaryContributor
from apps.notifications.models import (
    Notification,
    NotificationStatus,
    NotificationTemplate,
    NotificationType,
)
from apps.notifications.providers.factory import set_sms_provider_instance
from apps.notifications.providers.mock import MockSmsProvider
from apps.notifications.services import NotificationTemplateService


@pytest.fixture(autouse=True)
def setup_mock_sms():
    mock_sms = MockSmsProvider()
    set_sms_provider_instance(mock_sms)
    yield mock_sms
    mock_sms.clear()
    set_sms_provider_instance(None)


@pytest.fixture
def active_member(db):
    member = Member.objects.create(
        first_name="Nicholas",
        last_name="Appiah",
        email="nicholas@tcihlc.org",
        status=MemberStatus.ACTIVE,
    )
    MemberPhoneNumber.objects.create(
        member=member,
        phone_number="0241234567",
        is_primary=True,
        is_active=True,
    )
    return member


@pytest.fixture
def multi_phone_member(db):
    member = Member.objects.create(
        first_name="Ama",
        last_name="Osei",
        email="ama@tcihlc.org",
        status=MemberStatus.ACTIVE,
    )
    # Primary phone
    MemberPhoneNumber.objects.create(
        member=member,
        phone_number="0201112233",
        is_primary=True,
        is_active=True,
    )
    # Secondary phone
    MemberPhoneNumber.objects.create(
        member=member,
        phone_number="0559988776",
        is_primary=False,
        is_active=True,
    )
    return member


def test_01_registered_number_uses_member_first_name(seed_data, active_member):
    """1. Registered number matches member and personalizes with member's first name."""
    res = AutomaticPaymentService.process_payment_confirmation(
        provider_reference=f"TX-{uuid.uuid4().hex[:8]}",
        transaction_phone="0241234567",
        amount=Decimal("150.00"),
        contribution_type_name="Tithe",
        status=PaymentStatus.SUCCESSFUL,
        provider_name="MTN MOBILE MONEY",  # Provider name should NOT override registered member name
    )
    assert res["status"] == "SUCCESSFUL"
    assert res["contribution"].member == active_member
    assert res["contribution"].temporary_contributor is None

    notification = res["notification"]
    assert notification is not None
    assert "Dear Nicholas" in notification.message


def test_02_unregistered_number_with_provider_name_personalization(seed_data):
    """2. Unregistered number with provider name personalizes using provider first name."""
    ref = f"TX-{uuid.uuid4().hex[:8]}"
    res = AutomaticPaymentService.process_payment_confirmation(
        provider_reference=ref,
        transaction_phone="0501239999",
        amount=Decimal("75.00"),
        contribution_type_name="Offering",
        status=PaymentStatus.SUCCESSFUL,
        provider_name="Kwame Mensah",
    )
    assert res["status"] == "SUCCESSFUL"
    assert res["contribution"].member is None
    tc = res["contribution"].temporary_contributor
    assert tc is not None
    assert tc.provider_name == "Kwame Mensah"
    assert tc.phone_number == "0501239999"

    notification = res["notification"]
    assert notification is not None
    assert "Dear Kwame" in notification.message


def test_03_unregistered_number_without_name_uses_neutral_greeting(seed_data):
    """3. Unregistered number without provider name uses neutral greeting (Beloved)."""
    ref = f"TX-{uuid.uuid4().hex[:8]}"
    res = AutomaticPaymentService.process_payment_confirmation(
        provider_reference=ref,
        transaction_phone="0508884444",
        amount=Decimal("50.00"),
        contribution_type_name="Welfare",
        status=PaymentStatus.SUCCESSFUL,
        provider_name="",
    )
    assert res["status"] == "SUCCESSFUL"
    assert res["contribution"].member is None
    tc = res["contribution"].temporary_contributor
    assert tc is not None

    notification = res["notification"]
    assert notification is not None
    assert "Dear Beloved" in notification.message


def test_04_equivalent_phone_formats_match_correctly(seed_data, active_member):
    """4. Equivalent local and international phone formats match member correctly."""
    # Member has 0241234567. Inbound payment with +233241234567:
    res1 = AutomaticPaymentService.process_payment_confirmation(
        provider_reference=f"TX-{uuid.uuid4().hex[:8]}",
        transaction_phone="+233241234567",
        amount=Decimal("100.00"),
        contribution_type_name="Tithe",
        status=PaymentStatus.SUCCESSFUL,
    )
    assert res1["status"] == "SUCCESSFUL"
    assert res1["contribution"].member == active_member

    # Inbound payment with 233241234567:
    res2 = AutomaticPaymentService.process_payment_confirmation(
        provider_reference=f"TX-{uuid.uuid4().hex[:8]}",
        transaction_phone="233241234567",
        amount=Decimal("100.00"),
        contribution_type_name="Tithe",
        status=PaymentStatus.SUCCESSFUL,
    )
    assert res2["status"] == "SUCCESSFUL"
    assert res2["contribution"].member == active_member


def test_05_sms_recipient_is_actual_transaction_phone(seed_data, multi_phone_member):
    """5. SMS recipient is strictly the transaction phone, not primary phone."""
    # Multi-phone member: primary is 0201112233, transaction uses secondary 0559988776
    res = AutomaticPaymentService.process_payment_confirmation(
        provider_reference=f"TX-{uuid.uuid4().hex[:8]}",
        transaction_phone="0559988776",
        amount=Decimal("200.00"),
        contribution_type_name="Tithe",
        status=PaymentStatus.SUCCESSFUL,
    )
    assert res["status"] == "SUCCESSFUL"
    assert res["contribution"].member == multi_phone_member

    notification = res["notification"]
    assert notification.recipient_phone == "0559988776"
    assert notification.recipient_phone != multi_phone_member.phone_numbers.get(is_primary=True).phone_number


def test_06_category_templates_selected_for_tithe_offering_welfare(seed_data):
    """6. Tithe, Offering and Welfare select their respective category templates."""
    tithe_type = ContributionType.objects.get(name="Tithe")
    offering_type = ContributionType.objects.get(name="Offering")
    welfare_type = ContributionType.objects.get(name="Welfare")

    t_tithe = NotificationTemplateService.get_thank_you_template(tithe_type)
    assert t_tithe is not None
    assert t_tithe.contribution_type == tithe_type
    assert "Tithe" in t_tithe.template_body

    t_offering = NotificationTemplateService.get_thank_you_template(offering_type)
    assert t_offering is not None
    assert t_offering.contribution_type == offering_type
    assert "Offering" in t_offering.template_body

    t_welfare = NotificationTemplateService.get_thank_you_template(welfare_type)
    assert t_welfare is not None
    assert t_welfare.contribution_type == welfare_type
    assert "Welfare" in t_welfare.template_body


def test_07_editing_one_category_template_does_not_affect_another(seed_data, system_admin):
    """7. Editing one category's template does not change another category."""
    tithe_type = ContributionType.objects.get(name="Tithe")
    offering_type = ContributionType.objects.get(name="Offering")

    t_offering_before = NotificationTemplateService.get_thank_you_template(offering_type).template_body

    # Edit Tithe template
    t_tithe = NotificationTemplateService.get_thank_you_template(tithe_type)
    new_tithe_body = "Dear {first_name}, special thanks for your Tithe of {amount} {currency}! God bless!"
    NotificationTemplateService.save_template(
        template_id=t_tithe.id,
        name=t_tithe.name,
        notification_type=NotificationType.THANK_YOU,
        contribution_type=tithe_type,
        template_body=new_tithe_body,
        is_active=True,
        actor=system_admin,
    )

    # Check Tithe changed
    assert NotificationTemplateService.get_thank_you_template(tithe_type).template_body == new_tithe_body
    # Check Offering remained identical
    assert NotificationTemplateService.get_thank_you_template(offering_type).template_body == t_offering_before


def test_08_invalid_placeholders_rejected(seed_data, system_admin):
    """8. Invalid placeholders are rejected upon saving."""
    invalid_body = "Dear {first_name}, thank you for {amount} {currency} {bad_token_xyz}!"
    with pytest.raises(ValidationError) as exc:
        NotificationTemplateService.save_template(
            name="Invalid Template Test",
            notification_type=NotificationType.THANK_YOU,
            template_body=invalid_body,
            is_active=True,
            actor=system_admin,
        )
    assert "Unsupported placeholder" in str(exc.value)


def test_09_missing_category_template_uses_configured_fallback(seed_data):
    """9. Missing category template uses configured fallback."""
    # Other contribution type has no specific active template
    other_type = ContributionType.objects.get(name="Other")

    msg, template = NotificationTemplateService.build_thank_you_message(
        first_name="Kofi",
        amount=Decimal("120.00"),
        currency="GHS",
        contribution_type_name="Other",
        contribution_type=other_type,
    )
    # Falls back to General Contribution Thank You or generic template
    assert template is not None
    assert template.contribution_type is None  # Generic template
    assert "Kofi" in msg
    assert "120.00" in msg


def test_10_historical_messages_remain_unchanged_after_template_edits(seed_data, active_member, system_admin):
    """10. Historical messages retain their original wording when template is updated."""
    res = AutomaticPaymentService.process_payment_confirmation(
        provider_reference=f"TX-{uuid.uuid4().hex[:8]}",
        transaction_phone="0241234567",
        amount=Decimal("300.00"),
        contribution_type_name="Tithe",
        status=PaymentStatus.SUCCESSFUL,
    )
    notification = res["notification"]
    original_message = notification.message

    # Now edit Tithe template
    tithe_type = ContributionType.objects.get(name="Tithe")
    t_tithe = NotificationTemplateService.get_thank_you_template(tithe_type)
    NotificationTemplateService.save_template(
        template_id=t_tithe.id,
        name=t_tithe.name,
        notification_type=NotificationType.THANK_YOU,
        contribution_type=tithe_type,
        template_body="Brand new wording for Tithe: {amount} from {first_name}",
        is_active=True,
        actor=system_admin,
    )

    # Reload notification from database
    notification.refresh_from_db()
    assert notification.message == original_message


def test_11_duplicate_callbacks_do_not_create_duplicate_notifications(seed_data, active_member):
    """11. Duplicate callbacks do not create duplicate notifications."""
    ref = f"TX-DUP-{uuid.uuid4().hex[:8]}"

    res1 = AutomaticPaymentService.process_payment_confirmation(
        provider_reference=ref,
        transaction_phone="0241234567",
        amount=Decimal("100.00"),
        contribution_type_name="Tithe",
        status=PaymentStatus.SUCCESSFUL,
    )
    initial_notif_count = Notification.objects.filter(
        related_record_type="Contribution",
        related_record_id=res1["contribution"].id,
    ).count()
    assert initial_notif_count == 1

    # Second callback with exact same ref
    res2 = AutomaticPaymentService.process_payment_confirmation(
        provider_reference=ref,
        transaction_phone="0241234567",
        amount=Decimal("100.00"),
        contribution_type_name="Tithe",
        status=PaymentStatus.SUCCESSFUL,
    )
    final_notif_count = Notification.objects.filter(
        related_record_type="Contribution",
        related_record_id=res1["contribution"].id,
    ).count()
    assert final_notif_count == 1


def test_12_unauthorized_users_cannot_edit_templates(seed_data, system_user):
    """12. Unauthorized users cannot edit templates."""
    tithe_type = ContributionType.objects.get(name="Tithe")
    t_tithe = NotificationTemplateService.get_thank_you_template(tithe_type)

    # Direct service call by unauthorized user
    with pytest.raises(ValidationError) as exc:
        NotificationTemplateService.save_template(
            template_id=t_tithe.id,
            name=t_tithe.name,
            notification_type=NotificationType.THANK_YOU,
            template_body="Hacked template body",
            actor=system_user,
        )
    assert "permission" in str(exc.value).lower()

    # Via HTTP client
    client = Client()
    client.force_login(system_user)
    resp = client.get(f"/notifications/templates/{t_tithe.id}/edit/")
    assert resp.status_code == 403


def test_13_successful_contribution_recorded_when_sms_fails(seed_data, active_member, setup_mock_sms):
    """13. Successful contribution remains recorded when SMS delivery fails."""
    ref = f"TX-{uuid.uuid4().hex[:8]}"
    setup_mock_sms.fail_next_n_times_for_phone("0241234567", 5)

    res = AutomaticPaymentService.process_payment_confirmation(
        provider_reference=ref,
        transaction_phone="0241234567",
        amount=Decimal("500.00"),
        contribution_type_name="Tithe",
        status=PaymentStatus.SUCCESSFUL,
    )

    # Contribution is recorded and confirmed
    contribution = res["contribution"]
    assert contribution is not None
    assert contribution.status == ContributionStatus.CONFIRMED
    assert contribution.amount == Decimal("500.00")

    # Receipt is created
    receipt = res["receipt"]
    assert receipt is not None
    assert receipt.receipt_number.startswith("HLC-")

    # Payment transaction is SUCCESSFUL
    payment_tx = res["payment_transaction"]
    assert payment_tx.status == PaymentTransactionStatus.SUCCESSFUL

    # Notification status reflects the SMS failure
    notification = res["notification"]
    assert notification.status in [NotificationStatus.FAILED, NotificationStatus.RETRYING]
