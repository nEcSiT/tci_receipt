import uuid
from django.db import models
from django.conf import settings
from django.core.exceptions import PermissionDenied


class ActorType(models.TextChoices):
    USER = "USER", "User"
    SYSTEM = "SYSTEM", "System"
    PROVIDER = "PROVIDER", "Provider"


class AuditResult(models.TextChoices):
    SUCCESS = "SUCCESS", "Success"
    FAILURE = "FAILURE", "Failure"
    PARTIAL = "PARTIAL", "Partial"


class AuditLog(models.Model):
    """
    Append-only audit trail recording system and user activities.
    Modification or deletion is strictly prohibited during normal operation.
    Table: audit_logs
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_logs"
    )
    actor_type = models.CharField(max_length=20, choices=ActorType.choices, default=ActorType.USER)
    action = models.CharField(max_length=100, db_index=True)
    entity_type = models.CharField(max_length=100, db_index=True)
    entity_id = models.UUIDField(db_index=True)
    old_values = models.JSONField(null=True, blank=True)
    new_values = models.JSONField(null=True, blank=True)
    reason = models.TextField(null=True, blank=True)
    result = models.CharField(max_length=30, choices=AuditResult.choices, default=AuditResult.SUCCESS)
    provider_reference = models.CharField(max_length=200, null=True, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "audit_logs"
        verbose_name = "Audit Log"
        verbose_name_plural = "Audit Logs"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["entity_type", "entity_id"]),
            models.Index(fields=["action", "created_at"]),
        ]

    def save(self, *args, **kwargs):
        # Enforce append-only immutability
        if self._state.adding is False and AuditLog.objects.filter(pk=self.pk).exists():
            raise PermissionDenied("Audit records are immutable and cannot be updated.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        # Enforce append-only immutability
        raise PermissionDenied("Audit records are append-only and cannot be physically deleted.")

    def __str__(self):
        actor = self.user.email if self.user else self.actor_type
        return f"[{self.created_at:%Y-%m-%d %H:%M}] {actor} - {self.action} on {self.entity_type}:{self.entity_id} ({self.result})"
