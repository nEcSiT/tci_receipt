from django.urls import path
from . import views

app_name = 'requisitions'
urlpatterns = [
    path('', views.queue, name='queue'),
    path('<uuid:requisition_id>/', views.detail, name='detail'),
    path('<uuid:requisition_id>/files/<uuid:file_id>/', views.staff_attachment, name='staff_attachment'),
    path('request/<str:token>/files/<uuid:file_id>/', views.attachment, name='attachment'),
    path('request/', views.portal, name='portal'),
    path('request/<str:token>/', views.requester, name='requester'),
]
