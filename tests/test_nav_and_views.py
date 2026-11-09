from django.urls import reverse
from apps.accounts.models import RolePermission, Permission
from apps.notifications.models import Notification, NotificationType, NotificationStatus


def test_notification_list_view(client, system_user):
    url = reverse("notifications:notification_list")

    # Anonymous user redirected
    response = client.get(url)
    assert response.status_code == 302

    # Authentication alone does not grant access to notification history
    client.force_login(system_user)
    assert client.get(url).status_code == 403
    role = system_user.user_roles.first().role
    RolePermission.objects.create(role=role, permission=Permission.objects.get(code="notification.view"))
    Notification.objects.create(
        notification_type=NotificationType.THANK_YOU,
        recipient_phone="+233200000002",
        channel="SMS",
        status=NotificationStatus.SENT,
        message="Thank you for your generous contribution.",
    )
    response = client.get(url)
    assert response.status_code == 200
    assert "System Notifications" in response.content.decode()
    assert "+233200000002" in response.content.decode()


def test_reports_routing(client, system_user):
    # Direct /reports/ URL routes to payment summary
    client.force_login(system_user)
    role = system_user.user_roles.first().role
    report_perm = Permission.objects.get(code="report.view")
    RolePermission.objects.create(role=role, permission=report_perm)

    response = client.get("/reports/")
    assert response.status_code == 200
    assert "Contributions by Payment Method" in response.content.decode()
