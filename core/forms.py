from django import forms
from django.utils import timezone
from .models import MeetingNote, Milestone, Person, Project, Risk, Task


class MeetingNoteForm(forms.ModelForm):
    intent = forms.ChoiceField(
        choices=(("save", "仅保存"), ("parse", "保存并开始解析")),
        required=False,
        initial="save",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.is_bound and not self.instance.pk:
            self.initial.setdefault("meeting_date", timezone.localdate())

    class Meta:
        model = MeetingNote
        fields = ["title", "meeting_date", "raw_text"]
        widgets = {
            "meeting_date": forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
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
        widgets = {field: forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}) for field in ("planned_start_date", "due_date", "acceptance_date")}


class TaskProgressForm(forms.Form):
    status = forms.ChoiceField(label="状态", choices=Task.Status.choices, required=False)
    progress = forms.IntegerField(label="进度", min_value=0, max_value=100, required=False)
    due_date = forms.DateField(label="截止日期", required=False, widget=forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}))
    current_note = forms.CharField(label="当前说明", required=False, widget=forms.Textarea(attrs={"rows": 2}))
    completed_work = forms.CharField(label="本次完成", required=False, widget=forms.Textarea(attrs={"rows": 2}))
    next_step = forms.CharField(label="下一步", required=False, widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args, task=None, **kwargs):
        super().__init__(*args, **kwargs)
        if task is not None:
            self.initial.update({
                "status": task.status,
                "progress": task.progress,
                "due_date": task.due_date,
                "current_note": task.current_note,
            })

    def clean(self):
        cleaned_data = super().clean()
        for field in ("status", "progress", "due_date", "current_note"):
            if field not in self.data and field in self.initial:
                cleaned_data[field] = self.initial[field]
        return cleaned_data


class RiskForm(forms.ModelForm):
    class Meta:
        model = Risk
        fields = ["project", "task", "risk_type", "content", "owner", "due_date", "status"]


class MilestoneForm(forms.ModelForm):
    class Meta:
        model = Milestone
        fields = ["project", "name", "description", "target_date", "status"]
