from django.urls import path
from . import weekplan_views

urlpatterns = [
    path("projects/<int:project_pk>/weekplans/", weekplan_views.weekplan_list, name="weekplan_list"),
    path("weekplans/<int:pk>/", weekplan_views.weekplan_detail, name="weekplan_detail"),
]
