from django import template

register = template.Library()


@register.filter(name="has_perm")
def has_perm(user, perm_code: str) -> bool:
    """Template filter to test if an authenticated user has the given permission code."""
    if not user or not user.is_authenticated:
        return False
    return user.has_permission(perm_code)


@register.simple_tag(takes_context=True)
def user_has_perm(context, perm_code: str) -> bool:
    """Template tag to test permission code directly against request user."""
    request = context.get("request")
    if not request or not hasattr(request, "user") or not request.user.is_authenticated:
        return False
    return request.user.has_permission(perm_code)
