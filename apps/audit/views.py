from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import render
from django.views import View

from apps.accounts.decorators import AppPermissionRequiredMixin
from apps.audit.models import AuditLog, ActorType, AuditResult


class AuditLogListView(AppPermissionRequiredMixin, View):
    """
    TCI-HLC Design System Section 24:
    Audit records designed for traceability rather than visual decoration.
    Requires audit.view permission.
    """
    permission_code = "audit.view"

    def get(self, request):
        qs = AuditLog.objects.select_related("user").order_by("-created_at")

        query = request.GET.get("q", "").strip()
        if query:
            qs = qs.filter(
                Q(action__icontains=query)
                | Q(entity_type__icontains=query)
                | Q(reason__icontains=query)
                | Q(provider_reference__icontains=query)
                | Q(user__email__icontains=query)
                | Q(user__first_name__icontains=query)
                | Q(user__last_name__icontains=query)
            )

        actor_type = request.GET.get("actor_type")
        if actor_type in ActorType.values:
            qs = qs.filter(actor_type=actor_type)

        result = request.GET.get("result")
        if result in AuditResult.values:
            qs = qs.filter(result=result)

        action = request.GET.get("action")
        if action:
            qs = qs.filter(action=action)

        paginator = Paginator(qs, 25)
        page_number = request.GET.get("page")
        page_obj = paginator.get_page(page_number)

        distinct_actions = (
            AuditLog.objects.order_by("action")
            .values_list("action", flat=True)
            .distinct()[:50]
        )

        return render(
            request,
            "audit/log_list.html",
            {
                "page": page_obj,
                "actor_types": ActorType.choices,
                "audit_results": AuditResult.choices,
                "actions": distinct_actions,
            },
        )
