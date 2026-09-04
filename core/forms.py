from django import forms
from django.utils import timezone
from .models import MeetingNote, Milestone, Person, Project, Risk, Task


class MeetingNoteForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.is_bound and not self.instance.pk:
            self.initial.setdefault("meeting_date", timezone.localdate())

    class Meta:
        model = MeetingNote
        fields = ["title", "meeting_date", "raw_text"]
        widgets = {
            "meeting_date": forms.DateInput(attrs={"type": "date"}),
            "raw_text": forms.Textarea(attrs={"rows": 18, "placeholder": "粘贴钉钉群消息或会议记录……", "autofocus": True}),
        }


class ProjectForm(forms.ModelForm):
    class Meta:
        model = Project
        fields = ["name", "description", "status", "progress", "planned_start_date", "planned_end_date"]


class PersonForm(forms.ModelForm):
    class Meta:
        model = Person
        fields = ["name", "note", "is_active"]


class TaskForm(forms.ModelForm):
    class Meta:
        model = Task
        fields = ["project", "title", "description", "assignee", "planned_start_date", "due_date", "acceptance_date", "status", "priority", "progress", "current_note"]
        widgets = {field: forms.DateInput(attrs={"type": "date"}) for field in ("planned_start_date", "due_date", "acceptance_date")}


class RiskForm(forms.ModelForm):
    class Meta:
        model = Risk
        fields = ["project", "task", "risk_type", "content", "owner", "due_date", "status"]


class MilestoneForm(forms.ModelForm):
    class Meta:
        model = Milestone
        fields = ["project", "name", "description", "target_date", "status"]
