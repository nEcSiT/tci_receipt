import logging
from django.contrib import messages
from django.core.exceptions import ValidationError, PermissionDenied
from django.db.models import Q
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.generic import ListView, DetailView

from apps.accounts.decorators import permission_required, system_admin_required
from apps.accounts.models import User, Role, Permission, UserRole, RolePermission, PasswordResetToken, AdminRecoverySession
from apps.accounts.services import AuthService, AdminRecoveryService, UserService
from apps.audit.models import AuditLog

logger = logging.getLogger(__name__)


class LoginView(View):
    """Browser-based authentication view using TCI design identity."""

    def get(self, request):
        if request.user.is_authenticated:
            return redirect("core:dashboard")
        return render(request, "accounts/login.html", {
            "next": request.GET.get("next", ""),
        })

    def post(self, request):
        email = request.POST.get("email", "")
        password = request.POST.get("password", "")
        remember_me = request.POST.get("remember_me") == "on"
        next_url = request.POST.get("next") or "core:dashboard"

        user, error = AuthService.authenticate_and_login(
            request, email=email, password=password, remember_me=remember_me
        )

        if error:
            return render(request, "accounts/login.html", {
                "error": error,
                "email": email,
                "next": next_url,
            }, status=400 if "inactive" in error.lower() else 401)

        return redirect(next_url)


class LogoutView(View):
    """Terminates session and emits audit record."""

    def get(self, request):
        AuthService.logout(request)
        messages.info(request, "You have been logged out successfully.")
        return redirect("accounts:login")

    def post(self, request):
        return self.get(request)


class PasswordResetRequestView(View):
    """Initiates single-use password reset flow."""

    def get(self, request):
        return render(request, "accounts/password_reset_request.html")

    def post(self, request):
        email = request.POST.get("email", "")
        success, raw_token = AuthService.request_password_reset(email)

        # For development and test accessibility, pass token to template context if present
        return render(request, "accounts/password_reset_sent.html", {
            "email": email,
            "reset_token": raw_token,
        })


class PasswordResetConfirmView(View):
    """Processes valid token and updates user password."""

    def get(self, request, token):
        return render(request, "accounts/password_reset_confirm.html", {"token": token})

    def post(self, request, token):
        new_password = request.POST.get("new_password", "")
        confirm_password = request.POST.get("confirm_password", "")

        if new_password != confirm_password:
            return render(request, "accounts/password_reset_confirm.html", {
                "token": token,
                "error": "Passwords do not match.",
            }, status=400)

        success, error = AuthService.confirm_password_reset(token, new_password)
        if not success:
            return render(request, "accounts/password_reset_confirm.html", {
                "token": token,
                "error": error,
            }, status=400)

        messages.success(request, "Your password has been reset successfully. Please sign in with your new password.")
        return redirect("accounts:login")


class AdminRecoveryInitiateView(View):
    """Initiates multi-factor recovery for the System Administrator."""

    def get(self, request):
        return render(request, "accounts/recovery_initiate.html")

    def post(self, request):
        email = request.POST.get("email", "")
        session, email_otp, phone_otp, error = AdminRecoveryService.initiate_recovery(email)

        if error:
            return render(request, "accounts/recovery_initiate.html", {
                "error": error,
                "email": email,
            }, status=400)

        request.session["recovery_session_token"] = session.session_token
        # In development/test, keep raw OTPs in session for validation flow
        request.session["dev_email_otp"] = email_otp
        request.session["dev_phone_otp"] = phone_otp

        return redirect("accounts:recovery_verify")


class AdminRecoveryVerifyView(View):
    """Verifies Email OTP followed by Phone OTP."""

    def get(self, request):
        session_token = request.session.get("recovery_session_token")
        if not session_token:
            messages.error(request, "No active recovery session found.")
            return redirect("accounts:recovery_initiate")

        session = AdminRecoverySession.objects.filter(session_token=session_token).first()
        if not session or not session.is_valid:
            messages.error(request, "Recovery session has expired.")
            return redirect("accounts:recovery_initiate")

        return render(request, "accounts/recovery_verify.html", {
            "session": session,
            "dev_email_otp": request.session.get("dev_email_otp"),
            "dev_phone_otp": request.session.get("dev_phone_otp"),
        })

    def post(self, request):
        session_token = request.session.get("recovery_session_token")
        session = AdminRecoverySession.objects.filter(session_token=session_token).first()
        if not session or not session.is_valid:
            messages.error(request, "Recovery session expired.")
            return redirect("accounts:recovery_initiate")

        step = request.POST.get("step")
        otp = request.POST.get("otp", "")

        if step == "email":
            success, error = AdminRecoveryService.verify_email_otp(session_token, otp)
            if not success:
                return render(request, "accounts/recovery_verify.html", {
                    "session": session,
                    "error": error,
                    "dev_email_otp": request.session.get("dev_email_otp"),
                    "dev_phone_otp": request.session.get("dev_phone_otp"),
                }, status=400)
            messages.success(request, "Email OTP verified. Now enter your Phone OTP.")
            return redirect("accounts:recovery_verify")

        elif step == "phone":
            success, error = AdminRecoveryService.verify_phone_otp(session_token, otp)
            if not success:
                return render(request, "accounts/recovery_verify.html", {
                    "session": session,
                    "error": error,
                    "dev_email_otp": request.session.get("dev_email_otp"),
                    "dev_phone_otp": request.session.get("dev_phone_otp"),
                }, status=400)
            messages.success(request, "Verification complete! You may now set a new password.")
            return redirect("accounts:recovery_reset")

        return redirect("accounts:recovery_verify")


class AdminRecoveryResetView(View):
    """Final password reset step of the System Administrator recovery process."""

    def get(self, request):
        session_token = request.session.get("recovery_session_token")
        session = AdminRecoverySession.objects.filter(session_token=session_token).first()
        if not session or not session.is_ready_for_reset:
            messages.error(request, "Verification incomplete or session expired.")
            return redirect("accounts:recovery_initiate")

        return render(request, "accounts/recovery_reset.html")

    def post(self, request):
        session_token = request.session.get("recovery_session_token")
        new_password = request.POST.get("new_password", "")
        confirm_password = request.POST.get("confirm_password", "")

        if new_password != confirm_password:
            return render(request, "accounts/recovery_reset.html", {
                "error": "Passwords do not match.",
            }, status=400)

        success, error = AdminRecoveryService.complete_recovery(session_token, new_password)
        if not success:
            return render(request, "accounts/recovery_reset.html", {
                "error": error,
            }, status=400)

        # Clear session recovery keys
        request.session.pop("recovery_session_token", None)
        request.session.pop("dev_email_otp", None)
        request.session.pop("dev_phone_otp", None)

        messages.success(request, "System Administrator password updated successfully. Please log in.")
        return redirect("accounts:login")


# ==============================================================================
# System User Management Views (Requires "user.manage" or System Administrator)
# ==============================================================================

class UserListView(View):
    """Lists system users with search, role filters, and active state controls."""

    def get(self, request):
        if not request.user.is_authenticated:
            return redirect(f"/login/?next={request.path}")
        if not (request.user.is_system_administrator or request.user.has_permission("user.manage")):
            raise PermissionDenied("You do not have permission to manage users.")

        query = request.GET.get("q", "").strip()
        status_filter = request.GET.get("status", "all")
        role_filter = request.GET.get("role", "all")

        users = User.objects.all().prefetch_related("user_roles__role")

        if query:
            users = users.filter(
                Q(first_name__icontains=query) |
                Q(last_name__icontains=query) |
                Q(email__icontains=query)
            )

        if status_filter == "active":
            users = users.filter(is_active=True)
        elif status_filter == "inactive":
            users = users.filter(is_active=False)

        if role_filter != "all":
            users = users.filter(user_roles__role__name=role_filter)

        roles = Role.objects.filter(is_active=True)

        return render(request, "accounts/user_list.html", {
            "users": users,
            "roles": roles,
            "query": query,
            "status_filter": status_filter,
            "role_filter": role_filter,
        })


class UserCreateView(View):
    """Creates a new System User."""

    def get(self, request):
        if not request.user.is_authenticated:
            return redirect(f"/login/?next={request.path}")
        if not (request.user.is_system_administrator or request.user.has_permission("user.manage")):
            raise PermissionDenied("You do not have permission to create users.")

        roles = Role.objects.filter(is_active=True)
        return render(request, "accounts/user_form.html", {
            "roles": roles,
            "is_create": True,
        })

    def post(self, request):
        if not request.user.is_authenticated:
            return redirect(f"/login/?next={request.path}")
        if not (request.user.is_system_administrator or request.user.has_permission("user.manage")):
            raise PermissionDenied("You do not have permission to create users.")

        email = request.POST.get("email", "").strip()
        first_name = request.POST.get("first_name", "").strip()
        last_name = request.POST.get("last_name", "").strip()
        role_name = request.POST.get("role_name", Role.SYSTEM_USER)
        initial_password = request.POST.get("password", "").strip() or None

        try:
            user = UserService.create_system_user(
                creator=request.user,
                email=email,
                first_name=first_name,
                last_name=last_name,
                role_name=role_name,
                initial_password=initial_password,
            )
            messages.success(request, f"System User '{user.full_name}' was created successfully.")
            return redirect("accounts:user_list")
        except (ValidationError, Exception) as e:
            roles = Role.objects.filter(is_active=True)
            return render(request, "accounts/user_form.html", {
                "roles": roles,
                "is_create": True,
                "error": str(e),
                "form_data": request.POST,
            }, status=400)


class UserDetailView(View):
    """Inspects a single user, assigned roles, permissions, and recent audit activity."""

    def get(self, request, user_id):
        if not request.user.is_authenticated:
            return redirect(f"/login/?next={request.path}")
        if not (request.user.is_system_administrator or request.user.has_permission("user.manage")):
            raise PermissionDenied("You do not have permission to view user details.")

        user = get_object_or_404(User, id=user_id)
        user_audit_logs = AuditLog.objects.filter(entity_type="User", entity_id=user.id)[:10]

        return render(request, "accounts/user_detail.html", {
            "target_user": user,
            "user_audit_logs": user_audit_logs,
        })


class UserEditView(View):
    """Edits a System User's details and role."""

    def get(self, request, user_id):
        if not request.user.is_authenticated:
            return redirect(f"/login/?next={request.path}")
        if not (request.user.is_system_administrator or request.user.has_permission("user.manage")):
            raise PermissionDenied("You do not have permission to edit users.")

        user = get_object_or_404(User, id=user_id)
        roles = Role.objects.filter(is_active=True)
        return render(request, "accounts/user_form.html", {
            "target_user": user,
            "roles": roles,
            "is_create": False,
        })

    def post(self, request, user_id):
        if not request.user.is_authenticated:
            return redirect(f"/login/?next={request.path}")
        if not (request.user.is_system_administrator or request.user.has_permission("user.manage")):
            raise PermissionDenied("You do not have permission to edit users.")

        user = get_object_or_404(User, id=user_id)
        first_name = request.POST.get("first_name", "").strip()
        last_name = request.POST.get("last_name", "").strip()
        role_name = request.POST.get("role_name")

        try:
            UserService.update_system_user(
                actor=request.user,
                user=user,
                first_name=first_name,
                last_name=last_name,
                role_name=role_name,
            )
            messages.success(request, f"User '{user.full_name}' updated successfully.")
            return redirect("accounts:user_detail", user_id=user.id)
        except (ValidationError, Exception) as e:
            roles = Role.objects.filter(is_active=True)
            return render(request, "accounts/user_form.html", {
                "target_user": user,
                "roles": roles,
                "is_create": False,
                "error": str(e),
            }, status=400)


class UserToggleStatusView(View):
    """Soft deactivates or activates a system user."""

    def post(self, request, user_id):
        if not request.user.is_authenticated:
            return redirect(f"/login/?next={request.path}")
        if not (request.user.is_system_administrator or request.user.has_permission("user.manage")):
            raise PermissionDenied("You do not have permission to toggle user status.")

        user = get_object_or_404(User, id=user_id)
        action = request.POST.get("action")  # 'activate' or 'deactivate'
        reason = request.POST.get("reason", "")

        try:
            is_active = (action == "activate")
            UserService.toggle_user_status(actor=request.user, user=user, is_active=is_active, reason=reason)
            status_text = "activated" if is_active else "deactivated"
            messages.success(request, f"User '{user.full_name}' was successfully {status_text}.")
        except (ValidationError, Exception) as e:
            messages.error(request, str(e))

        return redirect("accounts:user_list")
