from django.contrib import admin
from .models import User, Role, Permission, UserRole, RolePermission, PasswordResetToken, AdminRecoverySession


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = ("email", "first_name", "last_name", "is_active", "created_at")
    search_fields = ("email", "first_name", "last_name")
    list_filter = ("is_active",)


@admin.register(Role)
class RoleAdmin(admin.ModelAdmin):
    list_display = ("name", "is_active", "created_at")


@admin.register(Permission)
class PermissionAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "created_at")
    search_fields = ("code", "name")


@admin.register(UserRole)
class UserRoleAdmin(admin.ModelAdmin):
    list_display = ("user", "role", "assigned_at", "assigned_by")


@admin.register(RolePermission)
class RolePermissionAdmin(admin.ModelAdmin):
    list_display = ("role", "permission", "assigned_at", "assigned_by")


@admin.register(PasswordResetToken)
class PasswordResetTokenAdmin(admin.ModelAdmin):
    list_display = ("user", "expires_at", "is_used", "created_at")
    list_filter = ("is_used",)


@admin.register(AdminRecoverySession)
class AdminRecoverySessionAdmin(admin.ModelAdmin):
    list_display = ("session_token", "email_verified", "phone_verified", "is_completed", "expires_at", "created_at")
    list_filter = ("email_verified", "phone_verified", "is_completed")
