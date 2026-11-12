from django.urls import path
from apps.notifications import views

app_name = "notifications"

urlpatterns = [
    path("", views.NotificationListView.as_view(), name="notification_list"),
    path("<uuid:notification_id>/", views.NotificationDetailView.as_view(), name="notification_detail"),
    path("templates/", views.NotificationTemplateListView.as_view(), name="template_list"),
    path("templates/create/", views.NotificationTemplateCreateView.as_view(), name="template_create"),
    path("templates/<uuid:template_id>/edit/", views.NotificationTemplateUpdateView.as_view(), name="template_edit"),
    path("templates/<uuid:template_id>/toggle/", views.NotificationTemplateToggleActiveView.as_view(), name="template_toggle_active"),
    path("templates/preview/", views.NotificationTemplatePreviewApiView.as_view(), name="template_preview"),
]
