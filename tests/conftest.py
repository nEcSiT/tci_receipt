import pytest
from django.core.management import call_command
from apps.accounts.models import User, Role, UserRole


@pytest.fixture(autouse=True)
def enable_db_access_for_all_tests(db):
    """Enables database access for all pytest tests."""
    pass


@pytest.fixture
def seed_data(db):
    """Runs database seed command before a test."""
    call_command("seed_db")


@pytest.fixture
def system_admin(seed_data):
    """Returns the single System Administrator user."""
    admin_role = Role.objects.get(name=Role.SYSTEM_ADMINISTRATOR)
    return UserRole.objects.get(role=admin_role).user


@pytest.fixture
def system_user(seed_data):
    """Creates and returns an active System User assigned to a custom role."""
    role = Role.objects.create(name="Finance Officer", description="Test finance role")
    from apps.accounts.models import Permission, RolePermission
    RolePermission.objects.create(role=role, permission=Permission.objects.get(code="contribution.create"))
    RolePermission.objects.create(role=role, permission=Permission.objects.get(code="requisition.view"))
    user = User.objects.create_user(
        email="officer@tcihlc.org",
        password="ValidPassword123!",
        first_name="Finance",
        last_name="Officer"
    )
    UserRole.objects.create(user=user, role=role)
    return user
