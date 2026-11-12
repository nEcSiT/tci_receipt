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
    path("temporary/<uuid:tc_id>/review/", views.TemporaryContributorReviewView.as_view(), name="temporary_review"),
    path("temporary/<uuid:tc_id>/register/", views.TemporaryContributorRegisterView.as_view(), name="temporary_register"),
    path("temporary/<uuid:tc_id>/link/", views.TemporaryContributorLinkView.as_view(), name="temporary_link"),
    path("temporary/<uuid:tc_id>/action/", views.TemporaryContributorActionView.as_view(), name="temporary_action"),
    path("temporary/linking-requests/", views.ContributorLinkingRequestListView.as_view(), name="linking_request_list"),
    path("temporary/linking-requests/<uuid:request_id>/review/", views.ContributorLinkingRequestReviewView.as_view(), name="linking_request_review"),
    path("merges/", views.MemberMergeListView.as_view(), name="merge_list"),
    path("merges/create/", views.MemberMergeCreateView.as_view(), name="merge_create"),
    path("merges/<uuid:request_id>/review/", views.MemberMergeReviewView.as_view(), name="merge_review"),
]
