from django.contrib import admin
from .models import NotificationTemplate, Notification, NotificationAttempt, ManualAction


@admin.register(NotificationTemplate)
class NotificationTemplateAdmin(admin.ModelAdmin):
    list_display = ("name", "notification_type", "contribution_type", "is_active", "created_at")
    list_filter = ("notification_type", "is_active")


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("notification_type", "recipient_phone", "channel", "status", "created_at")
    search_fields = ("recipient_phone", "message")
    list_filter = ("notification_type", "status", "channel")


@admin.register(NotificationAttempt)
class NotificationAttemptAdmin(admin.ModelAdmin):
    list_display = ("notification", "attempt_number", "status", "attempted_at")
    list_filter = ("status",)


@admin.register(ManualAction)
class ManualActionAdmin(admin.ModelAdmin):
    list_display = ("action_type", "priority", "status", "assigned_to", "created_at", "resolved_at")
    search_fields = ("description", "action_type")
    list_filter = ("action_type", "priority", "status")
