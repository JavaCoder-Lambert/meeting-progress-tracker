from django import forms
from django.utils import timezone
from .models import MeetingNote, Milestone, Person, Project, ProjectPhase, Risk, Task
from .services.task_editing import task_edit_baseline


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
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        unfinished = self.instance.tasks.exclude(status=Task.Status.DONE).count() if self.instance.pk else 0
        self.fields["status"].help_text = (
            (f"仍有 {unfinished} 个未完成任务。" if unfinished else "")
            + "归档后，任务和风险不再进入首页待办与按人催办；任务状态和历史记录保留。"
        )

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
    task_baseline = forms.CharField(label="任务基线", widget=forms.HiddenInput, required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        project_id = self.data.get("project") if self.is_bound else self.initial.get("project")
        project_id = str(project_id or "")
        self.fields["phase"].queryset = (ProjectPhase.objects.filter(project_id=int(project_id))
                                        if project_id.isdecimal() and len(project_id) < 12 else ProjectPhase.objects.none())
        self.fields["phase"].error_messages["invalid_choice"] = "阶段必须属于当前项目，请重新选择。"
        self.fields["phase"].empty_label = "未归属阶段"
        self.fields["assignee"].empty_label = "未指定"
        self.fields["task_baseline"].required = bool(self.instance.pk)
        self.fields["task_baseline"].error_messages["required"] = "任务基线缺失，请重新核对当前任务。"
        if self.instance.pk and not self.is_bound:
            self.initial["task_baseline"] = task_edit_baseline(self.instance)

    def clean(self):
        cleaned = super().clean()
        project = cleaned.get("project")
        if self.instance.pk and project and self.instance.risks.exclude(project=project).exists():
            self.add_error("project", "任务仍关联其他项目的风险，请先调整关联后再移动任务。")
        if cleaned.get("status") == Task.Status.DONE and "progress" in cleaned:
            cleaned["progress"] = 100
        return cleaned

    class Meta:
        model = Task
        fields = ["project", "phase", "title", "description", "assignee", "planned_for", "planned_start_date", "due_date", "acceptance_date", "status", "priority", "progress", "current_note"]
        labels = {"project": "所属项目", "assignee": "负责人", "title": "任务名称", "progress": "进度（%）"}
        widgets = {
            **{field: forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}) for field in ("planned_for", "planned_start_date", "due_date", "acceptance_date")},
            "description": forms.Textarea(attrs={"rows": 3}),
            "current_note": forms.Textarea(attrs={"rows": 2, "placeholder": "当前进展、阻塞或需要留意的事"}),
        }


class TaskProgressForm(forms.Form):
    task_baseline = forms.CharField(label="任务基线", widget=forms.HiddenInput,
                                    error_messages={"required": "任务基线缺失，请重新核对当前任务。"})
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
                "task_baseline": task_edit_baseline(task),
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
    task_baseline = forms.CharField(label="任务基线", widget=forms.HiddenInput,
                                    error_messages={"required": "任务基线缺失，请重新核对当前任务。"})
    planned_for = forms.DateField(label="安排日期", required=False, widget=forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}))
    action = forms.ChoiceField(choices=(("date", "指定日期"), ("today", "今天"), ("next_week", "下周一"), ("clear", "取消安排")))

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("action") == "date" and not cleaned.get("planned_for"):
            self.add_error("planned_for", "请选择安排日期。")
        return cleaned
