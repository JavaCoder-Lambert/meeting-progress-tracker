from django import forms
from django.utils import timezone
from .models import MeetingNote, Milestone, Person, Project, ProjectPhase, Risk, Task


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
    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("planned_start_date"), cleaned.get("planned_end_date")
        if start and end and start > end:
            self.add_error("planned_end_date", "计划完成不能早于计划开始。")
        return cleaned

    class Meta:
        model = Project
        fields = ["name", "description", "status", "progress", "planned_start_date", "planned_end_date"]
        widgets = {field: forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}) for field in ("planned_start_date", "planned_end_date")}


class PersonForm(forms.ModelForm):
    class Meta:
        model = Person
        fields = ["name", "note", "is_active"]


class TaskForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["phase"].queryset = ProjectPhase.objects.select_related("project").all()

    def clean(self):
        cleaned = super().clean()
        project = cleaned.get("project")
        if self.instance.pk and project and self.instance.risks.exclude(project=project).exists():
            self.add_error("project", "任务仍关联其他项目的风险，请先调整关联后再移动任务。")
        return cleaned

    class Meta:
        model = Task
        fields = ["project", "phase", "title", "description", "assignee", "planned_for", "planned_start_date", "due_date", "acceptance_date", "status", "priority", "progress", "current_note"]
        widgets = {field: forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}) for field in ("planned_for", "planned_start_date", "due_date", "acceptance_date")}


class TaskProgressForm(forms.Form):
    status = forms.ChoiceField(label="状态", choices=Task.Status.choices, required=False)
    progress = forms.IntegerField(label="进度", min_value=0, max_value=100, required=False)
    due_date = forms.DateField(label="截止日期", required=False, widget=forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}))
    current_note = forms.CharField(label="当前说明", required=False, widget=forms.Textarea(attrs={"rows": 2}))
    completed_work = forms.CharField(label="本次完成", required=False, widget=forms.Textarea(attrs={"rows": 2}))
    next_step = forms.CharField(label="下一步", required=False, widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args, task=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.task = task
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
        due_date = cleaned_data.get("due_date")
        if self.task and self.task.planned_start_date and due_date and due_date < self.task.planned_start_date:
            self.add_error("due_date", "截止日期不能早于计划开始。")
        return cleaned_data


class RiskForm(forms.ModelForm):
    def __init__(self, *args, project=None, **kwargs):
        super().__init__(*args, **kwargs)
        if project:
            self.fields["task"].queryset = Task.objects.filter(project=project)
            self.fields["project"].queryset = Project.objects.filter(pk=project.pk)

    def clean(self):
        cleaned = super().clean()
        task, project = cleaned.get("task"), cleaned.get("project")
        if task and project and task.project_id != project.pk:
            self.add_error("task", "关联任务必须属于当前项目。")
        return cleaned

    class Meta:
        model = Risk
        fields = ["project", "task", "risk_type", "content", "owner", "due_date", "status"]
        widgets = {"due_date": forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"})}


class MilestoneForm(forms.ModelForm):
    class Meta:
        model = Milestone
        fields = ["project", "name", "description", "target_date", "status"]
        widgets = {"target_date": forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"})}


class ProjectPhaseForm(forms.ModelForm):
    class Meta:
        model = ProjectPhase
        fields = ["name", "description", "position", "status", "start_date", "end_date"]
        widgets = {field: forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}) for field in ("start_date", "end_date")}


class TaskScheduleForm(forms.Form):
    planned_for = forms.DateField(label="安排日期", required=False, widget=forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}))
    action = forms.ChoiceField(choices=(("date", "指定日期"), ("today", "今天"), ("next_week", "下周一"), ("clear", "取消安排")))

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("action") == "date" and not cleaned.get("planned_for"):
            self.add_error("planned_for", "请选择安排日期。")
        return cleaned
