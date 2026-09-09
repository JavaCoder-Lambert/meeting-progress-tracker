from django.urls import path
from . import history_views as views

urlpatterns = [
    path("meetings/<int:pk>/history/", views.meeting_history, name="meeting_history"),
    path("projects/<int:pk>/history/", views.project_history, name="project_history"),
    path("progress/<int:pk>/correct/", views.progress_correction, name="progress_correction"),
    path("meetings/manual/<int:pk>/followups/", views.session_followups, name="session_followups"),
]
