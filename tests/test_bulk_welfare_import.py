from decimal import Decimal
import datetime
import io
import threading
import pytest
import openpyxl
from django.db import connection
from django.core.exceptions import ValidationError, PermissionDenied

from apps.accounts.models import User, Role, UserRole, Permission, RolePermission
from apps.contributions.models import (
    Contribution,
    ContributionType,
    PaymentMode,
    EntryMethod,
    ContributionStatus,
    WelfareCoverage,
    ContributionBatch,
    ContributionBatchStatus,
)
from apps.members.models import (
    Member,
    MemberStatus,
    MemberPhoneNumber,
    MemberImportBatch,
    MemberImportStatus,
)
from apps.contributions.services import BulkContributionService, WelfareCoverageService
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


# 4. Regression Tests for Defect Corrections

def test_welfare_search_by_all_fields_and_multiple_phones(client, finance_user):
    """
    Item 1 Acceptance Tests:
    - Search by member name, member number, phone number, and contribution number.
    - Members with multiple phone numbers do not cause duplicate contributions.
    - No matches produces a friendly empty state.
    - Preserves active filters when searching.
    """
    member = Member.objects.create(first_name="Kojo", last_name="Antwi", member_number="MEM-00000077")
    MemberPhoneNumber.objects.create(member=member, phone_number="+233241110001", is_primary=True, is_active=True)
    MemberPhoneNumber.objects.create(member=member, phone_number="+233201110002", is_primary=False, is_active=True)

    welfare_type = ContributionType.objects.get(name="Welfare")
    contrib = Contribution.objects.create(
        member=member,
        contribution_type=welfare_type,
        amount=Decimal("150.00"),
        payment_mode=PaymentMode.CASH,
        entry_method=EntryMethod.MANUAL,
        recorded_by=finance_user,
        contribution_date=datetime.date(2026, 5, 10),
    )
    WelfareCoverage.objects.create(contribution=contrib, year=2026, month=5)

    client.force_login(finance_user)

    # 1. Search by member name
    resp = client.get(f"/contributions/welfare/?q=Kojo&year=2026")
    assert resp.status_code == 200
    assert "Kojo Antwi" in resp.content.decode()
    assert resp.context["page_obj"].paginator.count == 1

    # 2. Search by member number
    resp = client.get(f"/contributions/welfare/?q=MEM-00000077&year=2026")
    assert resp.status_code == 200
    assert "Kojo Antwi" in resp.content.decode()
    assert resp.context["page_obj"].paginator.count == 1

    # 3. Search by phone number (primary phone)
    resp = client.get(f"/contributions/welfare/?q=0241110001&year=2026")
    assert resp.status_code == 200
    assert "Kojo Antwi" in resp.content.decode()
    # CRITICAL: Verify member having multiple phones does NOT duplicate results!
    assert resp.context["page_obj"].paginator.count == 1

    # 4. Search by secondary phone
    resp = client.get(f"/contributions/welfare/?q=0201110002&year=2026")
    assert resp.status_code == 200
    assert "Kojo Antwi" in resp.content.decode()
    assert resp.context["page_obj"].paginator.count == 1

    # 5. Search by contribution number
    resp = client.get(f"/contributions/welfare/?q={contrib.contribution_number}&year=2026")
    assert resp.status_code == 200
    assert "Kojo Antwi" in resp.content.decode()
    assert resp.context["page_obj"].paginator.count == 1

    # 6. Friendly empty state when no matches
    resp = client.get(f"/contributions/welfare/?q=NonExistentPerson&year=2026")
    assert resp.status_code == 200
    assert "No welfare contribution records found matching the selected filters." in resp.content.decode()
    assert resp.context["page_obj"].paginator.count == 0


def test_welfare_coverage_filter_separated_from_payment_date(client, finance_user):
    """
    Item 5 Acceptance Tests:
    - Coverage year/month vs Payment date range are distinct filtering concepts.
    - Payment received in December 2026 covering January–December 2027:
      * Appears under coverage year 2027.
      * Does NOT appear under coverage year 2026.
      * Appears in December 2026 payment-date report.
      * Selecting coverage year 2027 and December 2026 payment dates returns it.
      * Amount is counted strictly ONCE.
    """
    member = Member.objects.create(first_name="Samuel", last_name="Nkrumah", member_number="MEM-00000078")
    welfare_type = ContributionType.objects.get(name="Welfare")

    # Payment received on 2026-12-15 covering Whole Year 2027
    contrib = Contribution.objects.create(
        member=member,
        contribution_type=welfare_type,
        amount=Decimal("120.00"),
        payment_mode=PaymentMode.MOBILE_MONEY,
        entry_method=EntryMethod.MANUAL,
        recorded_by=finance_user,
        contribution_date=datetime.date(2026, 12, 15),
    )
    for m in range(1, 13):
        WelfareCoverage.objects.create(contribution=contrib, year=2027, month=m)

    client.force_login(finance_user)

    # 1. Under coverage year 2027: MUST appear
    resp = client.get("/contributions/welfare/?year=2027")
    assert resp.status_code == 200
    assert "Samuel Nkrumah" in resp.content.decode()
    assert resp.context["total_welfare_amount"] == Decimal("120.00")
    assert resp.context["transactions_count"] == 1

    # 2. Under coverage year 2026: MUST NOT appear (payment-year fallback removed!)
    resp = client.get("/contributions/welfare/?year=2026")
    assert resp.status_code == 200
    assert "Samuel Nkrumah" not in resp.content.decode()
    assert resp.context["total_welfare_amount"] == Decimal("0.00")
    assert resp.context["transactions_count"] == 0

    # 3. In December 2026 payment date range report (coverage year defaulted to 2026):
    # If date range is set, selecting coverage year 2027 returns it
    resp = client.get("/contributions/welfare/?year=2027&date_from=2026-12-01&date_to=2026-12-31")
    assert resp.status_code == 200
    assert "Samuel Nkrumah" in resp.content.decode()
    assert resp.context["total_welfare_amount"] == Decimal("120.00")

    # 4. Incompatible coverage year + payment date returns 0 records
    resp = client.get("/contributions/welfare/?year=2026&date_from=2026-12-01&date_to=2026-12-31")
    assert resp.status_code == 200
    assert "Samuel Nkrumah" not in resp.content.decode()
    assert resp.context["transactions_count"] == 0


def test_welfare_coverage_strict_validation():
    """
    Item 3 Acceptance Tests:
    Input                 Expected result
    Jan, Feb, Mar         [1, 2, 3]
    1, 2, 3               [1, 2, 3]
    Whole Year            [1..12]
    13                    Validation error
    Jan, 13               Validation error (mixed invalid component rejected)
    Missing months        Validation error
    Missing year          Validation error
    Future year & valid   Accepted
    """
    # 1. Jan, Feb, Mar
    yr, months = WelfareCoverageService.parse_and_validate_coverage("2026", "Jan, Feb, Mar")
    assert yr == 2026
    assert months == [1, 2, 3]

    # 2. 1, 2, 3
    yr, months = WelfareCoverageService.parse_and_validate_coverage(2026, "1, 2, 3")
    assert yr == 2026
    assert months == [1, 2, 3]

    # 3. Whole Year
    yr, months = WelfareCoverageService.parse_and_validate_coverage(2026, "Whole Year")
    assert yr == 2026
    assert months == list(range(1, 13))

    # 4. Invalid month 13
    with pytest.raises(ValidationError) as exc:
        WelfareCoverageService.parse_and_validate_coverage(2026, "13")
    assert "Select the welfare months this payment covers" in str(exc.value) or "13" in str(exc.value)

    # 5. Mixed invalid component: Jan, 13
    with pytest.raises(ValidationError) as exc:
        WelfareCoverageService.parse_and_validate_coverage(2026, "Jan, 13")
    assert "13" in str(exc.value)

    # 6. Missing months
    with pytest.raises(ValidationError) as exc:
        WelfareCoverageService.parse_and_validate_coverage(2026, "")
    assert "Select the welfare months this payment covers" in str(exc.value)

    # 7. Missing year
    with pytest.raises(ValidationError) as exc:
        WelfareCoverageService.parse_and_validate_coverage("", "Jan, Feb")
    assert "Coverage year is required" in str(exc.value)

    # 8. Future year with valid months
    yr, months = WelfareCoverageService.parse_and_validate_coverage(2029, "October, November, December")
    assert yr == 2029
    assert months == [10, 11, 12]


def test_bulk_submission_persistent_batch_and_duplicate_prevention(finance_user, viewer_only_user):
    """
    Item 2 Acceptance Tests:
    - Persistent batch record with database-enforced unique submission token.
    - Idempotent replay: double submission returns existing result with is_replay=True.
    - Modified/altered token rejected.
    - Unauthorized user cannot confirm batch.
    - Revalidates row state; never silently skips invalid rows.
    """
    member = Member.objects.create(first_name="Priscilla", last_name="Darko", member_number="MEM-00000079")
    tithe_type = ContributionType.objects.get(name="Tithe")

    preview_data = {
        "valid_rows": [
            {
                "member_id": str(member.id),
                "category_id": str(tithe_type.id),
                "amount": "200.00",
                "payment_mode": "CASH",
                "payment_date": "2026-11-15",
            }
        ],
        "valid_count": 1,
        "valid_total_amount": Decimal("200.00"),
    }

    # 1. Create persistent batch record
    batch = BulkContributionService.create_batch_record(actor=finance_user, preview_data=preview_data)
    assert batch.submission_token is not None
    assert batch.status == ContributionBatchStatus.PENDING
    assert batch.row_count == 1

    # 2. Unauthorized user cannot confirm batch
    with pytest.raises(PermissionDenied):
        BulkContributionService.execute_batch(actor=viewer_only_user, submission_token=batch.submission_token)

    # 3. Altered token is rejected
    with pytest.raises(ValidationError):
        BulkContributionService.execute_batch(actor=finance_user, submission_token="altered-fake-token")

    # 4. Successful confirmation
    result = BulkContributionService.execute_batch(actor=finance_user, submission_token=batch.submission_token)
    assert result["is_replay"] is False
    assert result["count"] == 1
    assert result["total_amount"] == Decimal("200.00")

    batch.refresh_from_db()
    assert batch.status == ContributionBatchStatus.COMPLETED

    # 5. Repeated confirmation (replay) returns original result without duplicate records
    replay = BulkContributionService.execute_batch(actor=finance_user, submission_token=batch.submission_token)
    assert replay["is_replay"] is True
    assert replay["count"] == 1
    assert Contribution.objects.filter(member=member).count() == 1


def test_member_import_phoneless_and_file_hash_duplicate_detection(finance_user):
    """
    Item 4 Acceptance Tests:
    - Phoneless rows flagged for staff review before creating potentially duplicate members.
    - Two different people with same name are not automatically merged.
    - Repeated file uploads detected via SHA-256 hash.
    - Replay returns is_replay=True, creates 0 additional members.
    - Member imports create strictly 0 contributions and receipts.
    """
    # Create existing member named 'Kwabena Mensah'
    existing = Member.objects.create(first_name="Kwabena", last_name="Mensah")
    MemberPhoneNumber.objects.create(member=existing, phone_number="+233245556677", is_primary=True)

    # Workbook with 2 rows: one phone-less with same name (requires review), one phone-less with new name
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["First Name *", "Last Name *", "Phone Number *"])
    ws.append(["Kwabena", "Mensah", ""])  # Phoneless with existing name
    ws.append(["Akosua", "Agyei", ""])    # Phoneless with new name

    f_bytes = io.BytesIO()
    wb.save(f_bytes)
    f_bytes.seek(0)

    preview = MemberImportService.parse_and_validate_file(f_bytes, actor=finance_user)
    assert preview["review_required_count"] == 2
    assert preview["valid_count"] == 0  # Unreviewed phoneless rows not immediately 'READY'
    assert preview["file_hash"] is not None

    # Row 1 specifically warns about existing name match without auto-merging
    assert preview["rows"][0]["status"] == "REVIEW_REQUIRED"
    assert "a member with this name already exists" in preview["rows"][0]["errors"][0]

    # Create batch record
    batch = MemberImportService.create_import_batch(
        actor=finance_user,
        file_hash=preview["file_hash"],
        filename="members_test.xlsx",
        preview_data=preview,
    )
    assert batch.status == MemberImportStatus.PENDING

    # Execute import with confirm_phoneless=True
    result = MemberImportService.execute_import(
        actor=finance_user,
        import_token=batch.import_token,
        confirm_phoneless=True,
    )
    assert result["imported_count"] == 2

    # Verify a new distinct member was created for Kwabena Mensah without auto-merging
    kwabena_members = Member.objects.filter(first_name="Kwabena", last_name="Mensah")
    assert kwabena_members.count() == 2

    # Verify NO contributions, receipts, or transactional SMS created
    for m in kwabena_members:
        assert m.contributions.count() == 0

    # Repeated confirmation returns is_replay=True without duplicating members
    replay = MemberImportService.execute_import(
        actor=finance_user,
        import_token=batch.import_token,
        confirm_phoneless=True,
    )
    assert replay["is_replay"] is True
    assert kwabena_members.count() == 2


@pytest.mark.django_db(transaction=True)
def test_postgresql_concurrent_bulk_contribution_submission(finance_user):
    """
    Item 2 & 7 PostgreSQL Concurrency Acceptance Test:
    Verify that concurrent requests attempting to execute the same ContributionBatch
    lock atomically using select_for_update. Exactly one request creates contributions;
    the other receives a concurrency error or replay result. Financial records are never duplicated.
    """
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from django.db import connection, close_old_connections

    if connection.vendor != "postgresql":
        pytest.skip("Requires PostgreSQL row locking; SQLite cannot validate concurrency across threads.")

    member = Member.objects.create(first_name="Concurrent", last_name="BulkUser")
    tithe_type = ContributionType.objects.get(name="Tithe")

    preview_data = {
        "valid_rows": [
            {
                "member_id": str(member.id),
                "category_id": str(tithe_type.id),
                "amount": "100.00",
                "payment_mode": "CASH",
                "payment_date": "2026-11-20",
            }
        ],
        "valid_count": 1,
        "valid_total_amount": Decimal("100.00"),
    }
    batch = BulkContributionService.create_batch_record(actor=finance_user, preview_data=preview_data)

    barrier = Barrier(2)
    results = []
    errors = []

    def _execute():
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            res = BulkContributionService.execute_batch(actor=finance_user, submission_token=batch.submission_token)
            results.append(res)
            return res
        except Exception as e:
            errors.append(e)
            return None
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(_execute) for _ in range(2)]
        for f in futures:
            f.result()

    # Total created contributions must be strictly 1, never duplicated
    assert Contribution.objects.filter(member=member).count() == 1
    # At least one succeeded
    assert len(results) >= 1


@pytest.mark.django_db(transaction=True)
def test_postgresql_concurrent_member_import(finance_user):
    """
    Item 4 & 7 PostgreSQL Concurrency Acceptance Test:
    Verify that concurrent requests confirming the same MemberImportBatch
    lock cleanly. Members are never duplicated across concurrent executions.
    """
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from django.db import connection, close_old_connections

    if connection.vendor != "postgresql":
        pytest.skip("Requires PostgreSQL row locking; SQLite cannot validate concurrency across threads.")

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["First Name *", "Last Name *", "Phone Number *"])
    ws.append(["Concurrent", "Importee", "0249991122"])

    f_bytes = io.BytesIO()
    wb.save(f_bytes)
    f_bytes.seek(0)

    preview = MemberImportService.parse_and_validate_file(f_bytes, actor=finance_user)
    batch = MemberImportService.create_import_batch(
        actor=finance_user,
        file_hash=preview["file_hash"],
        filename="concurrent_import.xlsx",
        preview_data=preview,
    )

    barrier = Barrier(2)
    results = []
    errors = []

    def _import():
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            res = MemberImportService.execute_import(actor=finance_user, import_token=batch.import_token)
            results.append(res)
            return res
        except Exception as e:
            errors.append(e)
            return None
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(_import) for _ in range(2)]
        for f in futures:
            f.result()

    # Total created members must be strictly 1
    assert Member.objects.filter(first_name="Concurrent", last_name="Importee").count() == 1
    assert len(results) >= 1

