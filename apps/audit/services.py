from apps.audit.models import AuditLog, ActorType, AuditResult
from apps.audit.middleware import get_current_request, get_client_ip, get_current_user


class AuditService:
    """Service handling audit trail emission."""

    @classmethod
    def log(
        cls,
        action: str,
        entity_type: str,
        entity_id,
        user=None,
        actor_type: str = ActorType.USER,
        old_values: dict | None = None,
        new_values: dict | None = None,
        reason: str | None = None,
        result: str = AuditResult.SUCCESS,
        provider_reference: str | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> AuditLog:
        request = get_current_request()

        if user is None and request:
            user = get_current_user()

        if ip_address is None and request:
            ip_address = get_client_ip(request)

        if user_agent is None and request:
            user_agent = request.META.get("HTTP_USER_AGENT", "")

        return AuditLog.objects.create(
            user=user,
            actor_type=actor_type,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            old_values=old_values,
            new_values=new_values,
            reason=reason,
            result=result,
            provider_reference=provider_reference,
            ip_address=ip_address,
            user_agent=user_agent,
        )
