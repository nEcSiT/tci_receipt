from django.contrib import admin
from .models import Member, MemberPhoneNumber, TemporaryContributor, MemberMergeRequest


@admin.register(Member)
class MemberAdmin(admin.ModelAdmin):
    list_display = ("member_number", "first_name", "last_name", "email", "status", "created_at")
    search_fields = ("member_number", "first_name", "last_name", "email")
    list_filter = ("status",)


@admin.register(MemberPhoneNumber)
class MemberPhoneNumberAdmin(admin.ModelAdmin):
    list_display = ("phone_number", "member", "is_primary", "is_active", "provider")
    search_fields = ("phone_number", "member__first_name", "member__last_name", "member__member_number")
    list_filter = ("is_primary", "is_active")


@admin.register(TemporaryContributor)
class TemporaryContributorAdmin(admin.ModelAdmin):
    list_display = ("phone_number", "provider_name", "status", "created_at")
    search_fields = ("phone_number", "provider_name", "first_name", "last_name")
    list_filter = ("status",)


@admin.register(MemberMergeRequest)
class MemberMergeRequestAdmin(admin.ModelAdmin):
    list_display = ("source_member", "target_member", "status", "requested_by", "created_at")
    list_filter = ("status",)
