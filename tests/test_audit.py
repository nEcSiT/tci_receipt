import uuid
import pytest
from django.core.exceptions import PermissionDenied
from apps.audit.models import AuditLog, ActorType, AuditResult
from apps.audit.services import AuditService


def test_audit_log_creation(system_user):
    target_id = uuid.uuid4()
    log_entry = AuditService.log(
        action="contribution.create",
        entity_type="Contribution",
        entity_id=target_id,
        user=system_user,
        actor_type=ActorType.USER,
        new_values={"amount": "250.00"},
        result=AuditResult.SUCCESS
    )
    assert log_entry.action == "contribution.create"
    assert log_entry.entity_id == target_id
    assert log_entry.user == system_user


def test_audit_records_are_append_only(system_user):
    """Enforce Business Principle 26: Audit records are append-only during normal application operation."""
    log_entry = AuditService.log(
        action="user.login",
        entity_type="User",
        entity_id=system_user.id,
        user=system_user
    )

    # Attempt to modify
    log_entry.action = "user.tampered_action"
    with pytest.raises(PermissionDenied, match="Audit records are immutable and cannot be updated"):
        log_entry.save()

    # Attempt to delete
    with pytest.raises(PermissionDenied, match="Audit records are append-only and cannot be physically deleted"):
        log_entry.delete()
