from decimal import Decimal
import io
import pytest
import openpyxl
from django.core.exceptions import ValidationError, PermissionDenied

from apps.accounts.models import User, Role, UserRole, Permission, RolePermission
from apps.contributions.models import (
    Contribution,
    ContributionType,
    PaymentMode,
    EntryMethod,
    ContributionStatus,
    WelfareCoverage,
)
from apps.members.models import Member, MemberStatus, MemberPhoneNumber
from apps.contributions.services import BulkContributionService
from apps.members.services import MemberImportService
from apps.receipts.models import Receipt


@pytest.fixture
def sample_member(db):
    return Member.objects.create(first_name="Emmanuel", last_name="Quaye")


@pytest.fixture
def finance_user(seed_data):
    role = Role.objects.create(name="Finance Contrib Role", description="Role with contribution and member rights")
    perm_c = Permission.objects.get(code="contribution.create")
    perm_m = Permission.objects.get(code="member.create")
    perm_v = Permission.objects.get(code="contribution.view")
    RolePermission.objects.create(role=role, permission=perm_c)
    RolePermission.objects.create(role=role, permission=perm_m)
    RolePermission.objects.create(role=role, permission=perm_v)

    user = User.objects.create_user(
        email="finance_bulk@tcihlc.org",
        password="ValidPassword123!",
        first_name="Finance",
        last_name="Manager"
    )
    UserRole.objects.create(user=user, role=role)
    return user


@pytest.fixture
def viewer_only_user(seed_data):
    role = Role.objects.create(name="Viewer Role", description="View only role")
    perm_v = Permission.objects.get(code="contribution.view")
    RolePermission.objects.create(role=role, permission=perm_v)

    user = User.objects.create_user(
        email="viewer@tcihlc.org",
        password="ValidPassword123!",
        first_name="View",
        last_name="Only"
    )
    UserRole.objects.create(user=user, role=role)
    return user


# 1. Category Ordering and Other Description Tests
def test_category_ordering_puts_other_last(seed_data):
    categories = ContributionType.get_recording_categories()
    assert len(categories) >= 3
    assert categories[-1].name.lower() == "other"
    non_other_names = [c.name.lower() for c in categories[:-1]]
    assert "other" not in non_other_names
    assert "tithe" in non_other_names
    assert "welfare" in non_other_names


def test_other_contribution_requires_description(seed_data, sample_member, finance_user):
    other_type = ContributionType.objects.get(name="Other")
    
    # Blank description raises ValidationError
    with pytest.raises(ValidationError):
        contrib = Contribution(
            member=sample_member,
            contribution_type=other_type,
            amount=Decimal("100.00"),
            payment_mode=PaymentMode.CASH,
            entry_method=EntryMethod.MANUAL,
            custom_type_description="",
            recorded_by=finance_user,
        )
        contrib.clean()

    # Valid description works
    contrib = Contribution(
        member=sample_member,
        contribution_type=other_type,
        amount=Decimal("100.00"),
        payment_mode=PaymentMode.CASH,
        entry_method=EntryMethod.MANUAL,
        custom_type_description="Building Fund Donation",
        recorded_by=finance_user,
    )
    contrib.clean()
    contrib.save()
    assert contrib.custom_type_description == "Building Fund Donation"

    # Switching category away from Other clears description
    tithe_type = ContributionType.objects.get(name="Tithe")
    contrib.contribution_type = tithe_type
    contrib.clean()
    assert contrib.custom_type_description is None


# 2. Member Excel Import Tests
def test_member_import_template_and_parsing(finance_user):
    # Test template generation
    template_bytes = MemberImportService.generate_template()
    assert template_bytes is not None
    wb = openpyxl.load_workbook(io.BytesIO(template_bytes))
    sheet = wb.active
    headers = [cell.value for cell in sheet[1]]
    assert "First Name *" in headers
    assert "Last Name *" in headers
    assert "Phone Number *" in headers

    # Create test workbook with 2 rows (one leading-zero phone)
    wb_test = openpyxl.Workbook()
    ws_test = wb_test.active
    ws_test.append(["First Name *", "Last Name *", "Middle Name", "Phone Number *", "Email", "Ministry", "Team", "Department"])
    ws_test.append(["Kofi", "Mensah", "", "0241234567", "kofi@example.com", "Youth", "", "Media"])
    ws_test.append(["Ama", "Osei", "Akoto", "0509876543", "", "", "Choir", ""])

    test_file = io.BytesIO()
    wb_test.save(test_file)
    test_file.seek(0)

    # Validate file
    preview = MemberImportService.parse_and_validate_file(test_file, actor=finance_user)
    assert preview["total_rows"] == 2
    assert preview["valid_count"] == 2
    assert preview["error_count"] == 0

    # Execute import
    valid_rows = [r for r in preview["rows"] if r["status"] == "READY"]
    result = MemberImportService.execute_import(actor=finance_user, valid_rows=valid_rows)
    assert result["imported_count"] == 2

    # Check member exists and leading-zero preserved
    member_kofi = Member.objects.get(first_name="Kofi", last_name="Mensah")
    assert member_kofi.phone_numbers.filter(phone_number="0241234567").exists()
    assert member_kofi.contributions.count() == 0  # Crucial PRD rule: member import creates 0 contributions/receipts


def test_member_import_conflict_detection(finance_user):
    # Existing member with phone +233241112233
    existing = Member.objects.create(first_name="Existing", last_name="Member")
    MemberPhoneNumber.objects.create(member=existing, phone_number="+233241112233", is_primary=True)

    # Create workbook attempting to import with same phone
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["First Name *", "Last Name *", "Phone Number *"])
    ws.append(["Duplicate", "User", "0241112233"])

    test_file = io.BytesIO()
    wb.save(test_file)
    test_file.seek(0)

    preview = MemberImportService.parse_and_validate_file(test_file, actor=finance_user)
    assert preview["conflict_count"] == 1
    assert preview["rows"][0]["status"] == "CONFLICT"
    assert "already registered" in preview["rows"][0]["errors"][0]


# 3. Bulk Contribution Entry & Welfare Coverage Tests
def test_welfare_month_parsing():
    assert BulkContributionService.parse_welfare_months("Whole Year") == list(range(1, 13))
    assert BulkContributionService.parse_welfare_months("all") == list(range(1, 13))
    assert BulkContributionService.parse_welfare_months("Jan, Feb, Mar") == [1, 2, 3]
    assert BulkContributionService.parse_welfare_months("1, 2, 3") == [1, 2, 3]
    assert BulkContributionService.parse_welfare_months("October, November, December") == [10, 11, 12]
    assert BulkContributionService.parse_welfare_months("") == []


def test_bulk_contribution_validation_and_execution(finance_user):
    member = Member.objects.create(first_name="David", last_name="Baah", member_number="MEM-00000088")
    MemberPhoneNumber.objects.create(member=member, phone_number="+233248888888", is_primary=True)

    tithe_type = ContributionType.objects.get(name="Tithe")
    welfare_type = ContributionType.objects.get(name="Welfare")
    other_type = ContributionType.objects.get(name="Other")

    rows_data = [
        {
            "member_identifier": "MEM-00000088",
            "category_id": str(tithe_type.id),
            "amount": "150.00",
            "payment_mode": "MOMO",
            "payment_date": "2026-11-10",
            "reference_number": "REF-001",
        },
        {
            "member_identifier": "0248888888",
            "category_id": str(welfare_type.id),
            "amount": "120.00",
            "payment_mode": "CASH",
            "payment_date": "2026-11-10",
            "coverage_year": "2026",
            "coverage_months": "Whole Year",
        },
        {
            "member_identifier": "MEM-00000088",
            "category_id": str(other_type.id),
            "amount": "50.00",
            "payment_mode": "CASH",
            "payment_date": "2026-11-10",
            "custom_type_description": "Harvest Levy",
        },
    ]

    preview = BulkContributionService.parse_and_validate_rows(rows_data, actor=finance_user)
    assert preview["total_rows"] == 3
    assert preview["valid_count"] == 3
    assert preview["error_count"] == 0
    assert preview["total_amount"] == Decimal("320.00")

    # Execute batch
    token = "test-idempotency-token-123"
    result = BulkContributionService.execute_batch(
        actor=finance_user,
        valid_rows=preview["valid_rows"],
        idempotency_token=token,
    )

    assert result["count"] == 3
    assert result["total_amount"] == Decimal("320.00")
    assert len(result["receipts"]) == 3

    # Check welfare coverages
    welfare_contrib = Contribution.objects.get(contribution_type=welfare_type, member=member)
    assert welfare_contrib.welfare_coverages.count() == 12
    assert "Whole Year 2026 (Jan – Dec)" in welfare_contrib.welfare_coverage_display

    # Check receipt generation (TCI receipts use HLC- prefix)
    for contrib in result["contributions"]:
        assert hasattr(contrib, "receipt")
        assert contrib.receipt.receipt_number.startswith("HLC-")

    # Idempotency replay check
    replay_result = BulkContributionService.execute_batch(
        actor=finance_user,
        valid_rows=preview["valid_rows"],
        idempotency_token=token,
    )
    assert replay_result["is_replay"] is True
    assert replay_result["count"] == 3
    assert Contribution.objects.filter(member=member).count() == 3  # Not doubled


def test_permission_enforcement(viewer_only_user):
    # Viewer cannot parse or execute bulk contributions
    with pytest.raises(PermissionDenied):
        BulkContributionService.parse_and_validate_rows([], actor=viewer_only_user)

    with pytest.raises(PermissionDenied):
        BulkContributionService.execute_batch(actor=viewer_only_user, valid_rows=[], idempotency_token="tok")

    # Viewer cannot import members
    with pytest.raises(PermissionDenied):
        MemberImportService.execute_import(actor=viewer_only_user, valid_rows=[])


def test_welfare_overview_and_receipt_display(client, finance_user):
    member = Member.objects.create(first_name="Grace", last_name="Appiah", member_number="MEM-00000099")
    welfare_type = ContributionType.objects.get(name="Welfare")
    
    contrib = Contribution.objects.create(
        member=member,
        contribution_type=welfare_type,
        amount=Decimal("60.00"),
        payment_mode=PaymentMode.CASH,
        entry_method=EntryMethod.MANUAL,
        recorded_by=finance_user,
    )
    for m in [1, 2, 3]:
        WelfareCoverage.objects.create(contribution=contrib, year=2026, month=m)

    from apps.receipts.services import ReceiptService
    receipt = ReceiptService.generate_receipt(contrib, generated_by_user=finance_user, actor=finance_user)

    client.force_login(finance_user)

    # Test Welfare Overview view
    resp = client.get("/contributions/welfare/?year=2026")
    assert resp.status_code == 200
    assert "Welfare Contribution Management" in resp.content.decode()
    assert "Grace Appiah" in resp.content.decode()

    # Test Receipt Detail view renders coverage
    resp_receipt = client.get(f"/receipts/{receipt.id}/")
    assert resp_receipt.status_code == 200
    assert "Jan, Feb, Mar 2026" in resp_receipt.content.decode()
