from django.urls import path
from apps.accounts import views

app_name = "accounts"

urlpatterns = [
    # Authentication & Sessions
    path("login/", views.LoginView.as_view(), name="login"),
    path("logout/", views.LogoutView.as_view(), name="logout"),

    # Password Reset
    path("password-reset/", views.PasswordResetRequestView.as_view(), name="password_reset"),
    path("password-reset/confirm/<str:token>/", views.PasswordResetConfirmView.as_view(), name="password_reset_confirm"),

    # System Administrator Recovery (MFA)
    path("recovery/", views.AdminRecoveryInitiateView.as_view(), name="recovery_initiate"),
    path("recovery/verify/", views.AdminRecoveryVerifyView.as_view(), name="recovery_verify"),
    path("recovery/reset/", views.AdminRecoveryResetView.as_view(), name="recovery_reset"),

    # Role and permission management
    path("roles/", views.RoleListView.as_view(), name="role_list"),
    path("roles/<uuid:role_id>/permissions/", views.RolePermissionUpdateView.as_view(), name="role_permission_update"),

    # System User Administration
    path("users/", views.UserListView.as_view(), name="user_list"),
    path("users/create/", views.UserCreateView.as_view(), name="user_create"),
    path("users/<uuid:user_id>/", views.UserDetailView.as_view(), name="user_detail"),
    path("users/<uuid:user_id>/edit/", views.UserEditView.as_view(), name="user_edit"),
    path("users/<uuid:user_id>/toggle-status/", views.UserToggleStatusView.as_view(), name="user_toggle_status"),
]
