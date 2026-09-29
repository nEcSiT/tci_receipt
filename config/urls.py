from django.contrib import admin
from django.urls import path, include
from apps.accounts import views as account_views

urlpatterns = [
    path("admin/", admin.site.urls),

    # Direct top-level authentication and recovery routes
    path("login/", account_views.LoginView.as_view(), name="login"),
    path("logout/", account_views.LogoutView.as_view(), name="logout"),
    path("password-reset/", account_views.PasswordResetRequestView.as_view(), name="password_reset"),
    path("password-reset/confirm/<str:token>/", account_views.PasswordResetConfirmView.as_view(), name="password_reset_confirm"),
    path("recovery/", account_views.AdminRecoveryInitiateView.as_view(), name="recovery_initiate"),
    path("recovery/verify/", account_views.AdminRecoveryVerifyView.as_view(), name="recovery_verify"),
    path("recovery/reset/", account_views.AdminRecoveryResetView.as_view(), name="recovery_reset"),

    # Direct top-level user management routes
    path("users/", account_views.UserListView.as_view(), name="user_list"),
    path("users/create/", account_views.UserCreateView.as_view(), name="user_create"),
    path("users/<uuid:user_id>/", account_views.UserDetailView.as_view(), name="user_detail"),
    path("users/<uuid:user_id>/edit/", account_views.UserEditView.as_view(), name="user_edit"),
    path("users/<uuid:user_id>/toggle-status/", account_views.UserToggleStatusView.as_view(), name="user_toggle_status"),

    # Accounts application namespace
    path("accounts/", include("apps.accounts.urls")),

    # Core application
    path("", include("apps.core.urls")),
]
