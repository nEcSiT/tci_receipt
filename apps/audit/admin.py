from django.contrib import admin
from .models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "actor_type", "user", "action", "entity_type", "entity_id", "result")
    search_fields = ("action", "entity_type", "provider_reference", "reason")
    list_filter = ("actor_type", "action", "entity_type", "result")
    readonly_fields = [field.name for field in AuditLog._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
