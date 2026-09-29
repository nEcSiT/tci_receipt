from functools import wraps
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect
from django.contrib.auth.mixins import AccessMixin


def system_admin_required(view_func):
    """View decorator restricting access strictly to the single System Administrator."""
    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f"/login/?next={request.path}")
        if not request.user.is_system_administrator:
            raise PermissionDenied("Only the System Administrator may access this resource.")
        return view_func(request, *args, **kwargs)
    return _wrapped_view


def permission_required(permission_code: str):
    """View decorator ensuring active user has the specified permission."""
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped_view(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect(f"/login/?next={request.path}")
            if not request.user.has_permission(permission_code):
                raise PermissionDenied(f"Required permission '{permission_code}' is not granted.")
            return view_func(request, *args, **kwargs)
        return _wrapped_view
    return decorator


class SystemAdminRequiredMixin(AccessMixin):
    """CBV Mixin requiring the user to be the System Administrator."""

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if not request.user.is_system_administrator:
            raise PermissionDenied("Only the System Administrator may access this resource.")
        return super().dispatch(request, *args, **kwargs)


class AppPermissionRequiredMixin(AccessMixin):
    """CBV Mixin requiring a specific domain permission code."""
    permission_code = None

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if not self.permission_code or not request.user.has_permission(self.permission_code):
            raise PermissionDenied(f"Required permission '{self.permission_code}' is not granted.")
        return super().dispatch(request, *args, **kwargs)
