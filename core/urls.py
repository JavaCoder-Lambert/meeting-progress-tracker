from django.urls import path
from . import views

urlpatterns = [
    path("health/", views.health, name="health"),
    path("", views.dashboard, name="dashboard"),
    path("meetings/new/", views.meeting_create, name="meeting_create"),
    path("meetings/<int:pk>/", views.meeting_detail, name="meeting_detail"),
    path("meetings/<int:pk>/parse/", views.meeting_parse, name="meeting_parse"),
    path("drafts/<int:pk>/", views.draft_review, name="draft_review"),
    path("drafts/<int:pk>/confirm/", views.draft_confirm, name="draft_confirm"),
]
