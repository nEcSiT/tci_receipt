from django.urls import path
from apps.members import views

app_name = "members"

urlpatterns = [
    path("", views.MemberListView.as_view(), name="member_list"),
    path("create/", views.MemberCreateView.as_view(), name="member_create"),
    path("<uuid:member_id>/", views.MemberDetailView.as_view(), name="member_detail"),
    path("<uuid:member_id>/edit/", views.MemberEditView.as_view(), name="member_edit"),
    path("<uuid:member_id>/phones/add/", views.MemberPhoneAddView.as_view(), name="phone_add"),
    path("<uuid:member_id>/phones/<uuid:phone_id>/action/", views.MemberPhoneActionView.as_view(), name="phone_action"),
    path("<uuid:member_id>/status/", views.MemberStatusToggleView.as_view(), name="status_toggle"),
    path("temporary/", views.TemporaryContributorListView.as_view(), name="temporary_list"),
    path("temporary/<uuid:tc_id>/action/", views.TemporaryContributorActionView.as_view(), name="temporary_action"),
    path("merges/", views.MemberMergeListView.as_view(), name="merge_list"),
    path("merges/create/", views.MemberMergeCreateView.as_view(), name="merge_create"),
    path("merges/<uuid:request_id>/review/", views.MemberMergeReviewView.as_view(), name="merge_review"),
]
