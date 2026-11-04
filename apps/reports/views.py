import csv

from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Count, Q, Sum
from django.http import HttpResponse
from django.shortcuts import render
from django.urls import reverse
from django.views import View

from apps.accounts.decorators import AppPermissionRequiredMixin
from apps.contributions.models import Contribution, ContributionStatus, PaymentMode, PaymentTransactionStatus
from apps.reports.forms import PaymentSummaryFilters


class PaymentSummaryView(AppPermissionRequiredMixin, View):
    """PRD 18.1: confirmed receipts of funds, grouped without mixing currencies."""
    permission_code = "report.view"

    def get(self, request):
        exporting = request.GET.get("export") == "csv"
        if exporting and not request.user.has_permission("report.export"):
            raise PermissionDenied("Report export permission is required.")
        form = PaymentSummaryFilters(request.GET)
        context = {"filter_form": form}
        if not form.is_valid():
            return render(request, "reports/payment_summary.html", context, status=400)

        filters = form.cleaned_data
        # A linked gateway transaction must be successful; unresolved/duplicate
        # gateway records must never inflate received-income figures.
        contributions = Contribution.objects.filter(status=ContributionStatus.CONFIRMED).filter(
            Q(payment_transaction__isnull=True)
            | Q(payment_transaction__status=PaymentTransactionStatus.SUCCESSFUL)
        )
        for key, lookup in [("date_from", "contribution_date__gte"), ("date_to", "contribution_date__lte"),
                            ("contribution_type", "contribution_type"), ("entry_method", "entry_method"),
                            ("mode", "payment_mode"), ("currency", "currency")]:
            if filters.get(key):
                contributions = contributions.filter(**{lookup: filters[key]})

        rows = list(contributions.order_by().values("payment_mode", "currency").annotate(
            count=Count("id"), amount=Sum("amount")
        ).order_by("currency", "payment_mode"))
        totals = list(contributions.order_by().values("currency").annotate(
            count=Count("id"), amount=Sum("amount")
        ).order_by("currency"))
        params = request.GET.copy()
        for key in ("export", "page"):
            params.pop(key, None)
        for row in rows:
            row["label"] = PaymentMode(row["payment_mode"]).label
            drilldown = params.copy()
            drilldown["mode"] = row["payment_mode"]
            drilldown["currency"] = row["currency"]
            row["url"] = reverse("core:payment_summary") + "?" + drilldown.urlencode()

        if exporting:
            response = HttpResponse(content_type="text/csv; charset=utf-8")
            response["Content-Disposition"] = 'attachment; filename="contributions-by-payment-method.csv"'
            writer = csv.writer(response)
            writer.writerow(["Payment method", "Currency", "Contribution count", "Amount"])
            for row in rows:
                writer.writerow([row["label"], row["currency"], row["count"], format(row["amount"], ".2f")])
            return response

        export_params = params.copy()
        export_params["export"] = "csv"
        context.update({
            "summary_rows": rows, "currency_totals": totals,
            "page_obj": Paginator(contributions.select_related("contribution_type"), 25).get_page(request.GET.get("page")),
            "filter_query": params.urlencode(), "export_query": export_params.urlencode(),
            "can_export": request.user.has_permission("report.export"),
        })
        return render(request, "reports/payment_summary.html", context)
