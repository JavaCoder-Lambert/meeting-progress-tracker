from django.contrib import admin
from .models import ImportDraft, MeetingNote, Milestone, Person, ProgressUpdate, Project, Risk, Task

admin.site.register([Project, Person, MeetingNote, ImportDraft, Task, ProgressUpdate, Risk, Milestone])
