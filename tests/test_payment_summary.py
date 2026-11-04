import csv
import io
from decimal import Decimal
from urllib.parse import urlparse, parse_qs

import pytest
from django.urls import reverse

from apps.accounts.models import Permission, RolePermission
from apps.contributions.models import Contribution, ContributionType, PaymentTransaction
from apps.members.models import Member


@pytest.fixture
def ledger(seed_data):
    member = Member.objects.create(first_name="Test", last_name="Member")
    kind = ContributionType.objects.get(name="Tithe")

    def create(**overrides):
        fields = dict(member=member, contribution_type=kind, amount=Decimal("10.00"),
                      payment_mode="CASH", entry_method="MANUAL", contribution_date="2026-10-01")
        fields.update(overrides)
        return Contribution.objects.create(**fields)
    return create


def test_summary_reconciles_currencies_statuses_and_drilldown(client, system_admin, ledger):
    first = ledger(amount=Decimal("12.30"))
    ledger(amount=Decimal("7.70"))
    ledger(payment_mode="MOBILE_MONEY", amount=Decimal("30.00"))
    ledger(currency="USD", amount=Decimal("4.00"))
    for status in ("PENDING", "CANCELLED", "FLAGGED"):
        ledger(status=status, amount=Decimal("999.00"))
    for status in ("FAILED", "PENDING", "DUPLICATE", "CANCELLED"):
        txn = PaymentTransaction.objects.create(provider="test", provider_reference=status,
            transaction_phone="+233240001122", amount=Decimal("999.00"), status=status)
        ledger(payment_transaction=txn, entry_method="AUTOMATIC", amount=Decimal("999.00"))
    client.force_login(system_admin)
    response = client.get(reverse("core:payment_summary"))
    assert response.status_code == 200
    assert response.context["currency_totals"] == [
        {"currency": "GHS", "count": 3, "amount": Decimal("50.00")},
        {"currency": "USD", "count": 1, "amount": Decimal("4.00")},
    ]
    cash = next(r for r in response.context["summary_rows"] if r["payment_mode"] == "CASH" and r["currency"] == "GHS")
    detail = client.get(cash["url"])
    assert detail.context["page_obj"].paginator.count == 2
    assert first in detail.context["page_obj"]
    assert "999.00" not in response.content.decode()


def test_filters_use_contribution_date_and_include_inactive_types(client, system_admin, ledger):
    old_type = ContributionType.objects.create(name="Historic", is_active=False)
    keep = ledger(contribution_type=old_type, contribution_date="2026-09-30")
    ledger(contribution_type=old_type, contribution_date="2026-10-01")
    ledger(contribution_date="2026-09-30")
    client.force_login(system_admin)
    params = {"date_from": "2026-09-30", "date_to": "2026-09-30", "contribution_type": str(old_type.pk), "entry_method": "MANUAL"}
    response = client.get(reverse("core:payment_summary"), params)
    assert list(response.context["page_obj"]) == [keep]
    assert all(parse_qs(urlparse(r["url"]).query)["date_from"] == ["2026-09-30"] for r in response.context["summary_rows"])
    exported = client.get(reverse("core:payment_summary"), {**params, "export": "csv"})
    assert list(csv.reader(io.StringIO(exported.content.decode()))) == [
        ["Payment method", "Currency", "Contribution count", "Amount"], ["Cash", "GHS", "1", "10.00"]]


@pytest.mark.parametrize("params", [
    {"date_from": "invalid"}, {"date_from": "2026-10-02", "date_to": "2026-10-01"},
    {"contribution_type": "invalid"}, {"entry_method": "bad"}, {"mode": "bad"}, {"currency": "bad"},
])
def test_invalid_filters_do_not_silently_return_unfiltered_data(client, system_admin, ledger, params):
    ledger()
    client.force_login(system_admin)
    response = client.get(reverse("core:payment_summary"), params)
    assert response.status_code == 400
    assert "summary_rows" not in response.context


def test_permissions_apply_to_view_export_and_dashboard_link(client, system_user, ledger):
    ledger()
    url = reverse("core:payment_summary")
    assert client.get(url).status_code == 302
    client.force_login(system_user)
    assert client.get(url).status_code == 403
    assert client.get(url, {"export": "csv"}).status_code == 403
    assert url not in client.get(reverse("core:dashboard")).content.decode()
    role = system_user.user_roles.first().role
    RolePermission.objects.create(role=role, permission=Permission.objects.get(code="report.view"))
    assert client.get(url).status_code == 200
    assert client.get(url, {"export": "csv"}).status_code == 403
    assert url in client.get(reverse("core:dashboard")).content.decode()
    RolePermission.objects.create(role=role, permission=Permission.objects.get(code="report.export"))
    assert client.get(url, {"export": "csv"}).status_code == 200
    role.is_active = False
    role.save()
    assert client.get(url).status_code == 403


def test_empty_results_and_pagination(client, system_admin, ledger):
    client.force_login(system_admin)
    url = reverse("core:payment_summary")
    response = client.get(url)
    assert response.context["summary_rows"] == []
    assert "No confirmed contributions" in response.content.decode()
    for _ in range(26):
        ledger()
    response = client.get(url, {"page": 2})
    assert len(response.context["page_obj"]) == 1
    assert response.context["currency_totals"][0]["count"] == 26


def test_automatic_filter_and_all_payment_methods(client, system_admin, ledger):
    for mode in ("CASH", "MOBILE_MONEY", "CHEQUE", "BANK_TRANSACTION", "OTHER"):
        txn = PaymentTransaction.objects.create(provider="test", provider_reference=mode,
            transaction_phone="+233240001122", amount=Decimal("10.00"), status="SUCCESSFUL")
        ledger(payment_mode=mode, payment_transaction=txn, entry_method="AUTOMATIC")
    ledger()
    client.force_login(system_admin)
    response = client.get(reverse("core:payment_summary"), {"entry_method": "AUTOMATIC"})
    assert len(response.context["summary_rows"]) == 5
    assert response.context["currency_totals"][0]["count"] == 5
