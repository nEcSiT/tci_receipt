def permissions_context(request):
    """Provides user permissions and administrative role flags to templates."""
    if not hasattr(request, "user") or not request.user.is_authenticated:
        return {
            "user_permissions": set(),
            "is_system_administrator": False,
        }

    return {
        "user_permissions": request.user.get_all_permission_codes(),
        "is_system_administrator": request.user.is_system_administrator,
    }
