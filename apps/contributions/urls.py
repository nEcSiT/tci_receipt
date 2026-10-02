from django.urls import path
from apps.contributions import views

app_name = "contributions"

urlpatterns = [
    path("", views.ContributionListView.as_view(), name="contribution_list"),
    path("create/", views.ContributionCreateView.as_view(), name="contribution_create"),
    path("members/search/", views.MemberSearchApiView.as_view(), name="member_search"),
    path("<uuid:contribution_id>/", views.ContributionDetailView.as_view(), name="contribution_detail"),
]
