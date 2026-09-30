from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.contrib.staticfiles.urls import staticfiles_urlpatterns
from apps.accounts import views as account_views
from apps.contributions import views as contrib_views

urlpatterns = [
    # Direct top-level authentication and recovery routes
    path("login/", account_views.LoginView.as_view(), name="login"),
    path("logout/", account_views.LogoutView.as_view(), name="logout"),
    path("password-reset/", account_views.PasswordResetRequestView.as_view(), name="password_reset"),
    path("password-reset/confirm/<str:token>/", account_views.PasswordResetConfirmView.as_view(), name="password_reset_confirm"),
    path("recovery/", account_views.AdminRecoveryInitiateView.as_view(), name="recovery_initiate"),
    path("recovery/verify/", account_views.AdminRecoveryVerifyView.as_view(), name="recovery_verify"),
    path("recovery/reset/", account_views.AdminRecoveryResetView.as_view(), name="recovery_reset"),

    # Direct top-level user & role management routes
    path("users/", account_views.UserListView.as_view(), name="user_list"),
    path("users/create/", account_views.UserCreateView.as_view(), name="user_create"),
    path("users/<uuid:user_id>/", account_views.UserDetailView.as_view(), name="user_detail"),
    path("users/<uuid:user_id>/edit/", account_views.UserEditView.as_view(), name="user_edit"),
    path("users/<uuid:user_id>/toggle-status/", account_views.UserToggleStatusView.as_view(), name="user_toggle_status"),
    path("roles/", account_views.RoleListView.as_view(), name="role_list"),
    path("roles/<uuid:role_id>/permissions/", account_views.RolePermissionUpdateView.as_view(), name="role_permission_update"),

    # Accounts application namespace
    path("accounts/", include("apps.accounts.urls")),

    # Members application namespace
    path("members/", include("apps.members.urls")),

    # Contributions application namespace & direct routes
    path("contributions/", include("apps.contributions.urls")),
    path("contributions/", contrib_views.ContributionListView.as_view(), name="contribution_list"),
    path("contributions/create/", contrib_views.ContributionCreateView.as_view(), name="contribution_create"),
    path("contributions/<uuid:contribution_id>/", contrib_views.ContributionDetailView.as_view(), name="contribution_detail"),

    # Core application
    path("", include("apps.core.urls")),
]

if settings.DEBUG:
    urlpatterns += staticfiles_urlpatterns()
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

