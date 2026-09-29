import logging
from decimal import Decimal
from django.db import connection
from django.db.models import Sum
from django.core.cache import cache
from django.http import JsonResponse
from django.shortcuts import render, redirect
from django.utils import timezone
from django.views import View

from apps.accounts.models import User
from apps.audit.models import AuditLog
from apps.contributions.models import Contribution, ContributionStatus
from apps.receipts.models import Receipt
from apps.requisitions.models import Requisition, RequisitionStatus

logger = logging.getLogger(__name__)


def health_check(request):
    """
    Readiness and liveness probe checking:
    1. Application process
    2. Database connectivity
    3. Cache/Redis connectivity
    """
    status = {"status": "healthy", "components": {}}
    http_status = 200

    # 1. Database check
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1;")
            row = cursor.fetchone()
            if row and row[0] == 1:
                status["components"]["database"] = "healthy"
            else:
                status["components"]["database"] = "unhealthy"
                http_status = 503
    except Exception as e:
        logger.error(f"Healthcheck database error: {e}")
        status["components"]["database"] = f"unhealthy: {str(e)}"
        http_status = 503

    # 2. Cache / Redis check (graceful)
    try:
        cache.set("healthcheck_test_key", "ok", 10)
        cached_val = cache.get("healthcheck_test_key")
        if cached_val == "ok":
            status["components"]["cache"] = "healthy"
        else:
            status["components"]["cache"] = "degraded"
    except Exception as e:
        status["components"]["cache"] = f"unhealthy: {str(e)}"

    if http_status != 200:
        status["status"] = "unhealthy"

    return JsonResponse(status, status=http_status)


def index_view(request):
    """Landing redirector: sends authenticated users to dashboard and guests to login."""
    if request.user.is_authenticated:
        return redirect("core:dashboard")
    return redirect("accounts:login")


class DashboardView(View):
    """Primary operational dashboard overview matching TCI Design System (Section 15)."""

    def get(self, request):
        if not request.user.is_authenticated:
            return redirect(f"/login/?next={request.path}")

        today_start = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)

        # 1. Metric: Today's Contributions
        today_contributions = Contribution.objects.filter(
            created_at__gte=today_start,
            status=ContributionStatus.CONFIRMED,
        )
        today_contrib_count = today_contributions.count()
        today_contrib_total = today_contributions.aggregate(total=Sum("amount"))["total"] or Decimal("0.00")

        # 2. Metric: Receipts Generated
        receipts_count = Receipt.objects.count()

        # 3. Metric: Pending Requisitions
        pending_requisitions = Requisition.objects.filter(
            status__in=[
                RequisitionStatus.SUBMITTED,
                RequisitionStatus.UNDER_REVIEW,
                RequisitionStatus.PENDING_DISBURSEMENT,
            ]
        )
        pending_req_count = pending_requisitions.count()

        # 4. Metric: Manual Actions / Attention items
        manual_actions_count = Requisition.objects.filter(status=RequisitionStatus.EVIDENCE_SUBMITTED).count()

        # 5. Recent Activity Feed (Audit Log)
        recent_activities = AuditLog.objects.select_related("user").all()[:8]

        # Greeting logic
        current_hour = timezone.now().hour
        if current_hour < 12:
            greeting = "Good morning"
        elif current_hour < 17:
            greeting = "Good afternoon"
        else:
            greeting = "Good evening"

        return render(request, "dashboard/index.html", {
            "greeting": greeting,
            "today_contrib_count": today_contrib_count,
            "today_contrib_total": today_contrib_total,
            "receipts_count": receipts_count,
            "pending_req_count": pending_req_count,
            "manual_actions_count": manual_actions_count,
            "recent_activities": recent_activities,
            "pending_requisitions": pending_requisitions[:5],
        })
