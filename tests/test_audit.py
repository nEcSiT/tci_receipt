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


def test_audit_log_view_permissions(client, system_user, system_admin):
    from django.urls import reverse
    from apps.accounts.models import RolePermission, Permission

    url = reverse("audit:log_list")

    # Anonymous user redirected to login
    response = client.get(url)
    assert response.status_code == 302
    assert "/login/" in response.url

    # User without audit.view permission denied
    client.force_login(system_user)
    response = client.get(url)
    assert response.status_code == 403

    # User with audit.view permission allowed
    role = system_user.user_roles.first().role
    audit_perm = Permission.objects.get(code="audit.view")
    RolePermission.objects.create(role=role, permission=audit_perm)
    response = client.get(url)
    assert response.status_code == 200
    assert "System Audit Trail" in response.content.decode()

    # System administrator allowed
    client.force_login(system_admin)
    response = client.get(url)
    assert response.status_code == 200

    # Old admin URL redirects to audit list
    response = client.get("/admin/audit/auditlog/")
    assert response.status_code == 302
    assert response.url == "/audit/"

