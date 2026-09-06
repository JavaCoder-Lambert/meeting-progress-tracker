from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone


class TimestampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Project(TimestampedModel):
    class Status(models.TextChoices):
        NOT_STARTED = "not_started", "未开始"
        IN_PROGRESS = "in_progress", "进行中"
        PAUSED = "paused", "暂停"
        DONE = "done", "已完成"
        ARCHIVED = "archived", "已归档"

    name = models.CharField("项目名称", max_length=120, unique=True)
    description = models.TextField("说明", blank=True)
    status = models.CharField("状态", max_length=24, choices=Status.choices, default=Status.NOT_STARTED)
    progress = models.PositiveSmallIntegerField("总体进度", default=0, validators=[MinValueValidator(0), MaxValueValidator(100)])
    planned_start_date = models.DateField("计划开始", null=True, blank=True)
    planned_end_date = models.DateField("计划完成", null=True, blank=True)

    class Meta:
        ordering = ["status", "name"]

    def __str__(self):
        return self.name


class Person(models.Model):
    name = models.CharField("姓名", max_length=80, unique=True)
    note = models.TextField("备注", blank=True)
    is_active = models.BooleanField("在用", default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class ProjectPhase(TimestampedModel):
    class Status(models.TextChoices):
        NOT_STARTED = "not_started", "未开始"
        IN_PROGRESS = "in_progress", "进行中"
        DONE = "done", "已完成"
        DELAYED = "delayed", "已延期"

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="phases")
    name = models.CharField("阶段名称", max_length=120)
    description = models.TextField("交付内容", blank=True)
    position = models.PositiveSmallIntegerField("显示顺序", default=1)
    status = models.CharField("状态", max_length=24, choices=Status.choices, default=Status.NOT_STARTED)
    start_date = models.DateField("计划开始", null=True, blank=True)
    end_date = models.DateField("计划完成", null=True, blank=True)

    class Meta:
        ordering = ["position", "pk"]

    def clean(self):
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValidationError({"end_date": "计划完成不能早于计划开始。"})

    @property
    def is_overdue(self):
        return bool(self.end_date and self.end_date < timezone.localdate() and self.status != self.Status.DONE)

    def __str__(self):
        return f"{self.project.name} / {self.name}"


class MeetingNote(TimestampedModel):
    class ParseStatus(models.TextChoices):
        NOT_PARSED = "not_parsed", "未解析"
        PARSING = "parsing", "解析中"
        SUCCESS = "success", "解析成功"
        FAILED = "failed", "解析失败"
        IMPORTED = "imported", "已入库"

    title = models.CharField("标题", max_length=200)
    meeting_date = models.DateField("会议日期", db_index=True)
    raw_text = models.TextField("原始记录")
    parse_status = models.CharField("解析状态", max_length=20, choices=ParseStatus.choices, default=ParseStatus.NOT_PARSED)
    parse_error = models.TextField("解析错误", blank=True)
    raw_llm_response = models.TextField("模型原始响应", blank=True)

    class Meta:
        ordering = ["-meeting_date", "-created_at"]

    def __str__(self):
        return self.title


class MeetingSession(TimestampedModel):
    meeting_note = models.OneToOneField(MeetingNote, on_delete=models.CASCADE, related_name="manual_session")
    state = models.JSONField(default=dict)
    version = models.PositiveIntegerField(default=0)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    minutes = models.TextField(blank=True)


class ImportDraft(models.Model):
    meeting_note = models.ForeignKey(MeetingNote, on_delete=models.CASCADE, related_name="drafts")
    payload = models.JSONField("结构化草稿")
    created_at = models.DateTimeField(auto_now_add=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    review_state = models.JSONField("审阅暂存", default=dict, blank=True)
    review_saved_at = models.DateTimeField("暂存时间", null=True, blank=True)
    review_version = models.PositiveIntegerField("暂存版本", default=0)

    class Meta:
        ordering = ["-created_at"]


class Task(TimestampedModel):
    class Status(models.TextChoices):
        NOT_STARTED = "not_started", "未开始"
        IN_PROGRESS = "in_progress", "进行中"
        ACCEPTANCE = "acceptance", "待验收"
        DONE = "done", "已完成"
        DELAYED = "delayed", "已延期"
        BLOCKED = "blocked", "阻塞"

    class Priority(models.TextChoices):
        LOW = "low", "低"
        NORMAL = "normal", "普通"
        HIGH = "high", "高"
        URGENT = "urgent", "紧急"

    project = models.ForeignKey(Project, on_delete=models.PROTECT, related_name="tasks")
    phase = models.ForeignKey(ProjectPhase, null=True, blank=True, on_delete=models.SET_NULL, related_name="tasks", verbose_name="所属阶段")
    planned_for = models.DateField("安排日期", null=True, blank=True, db_index=True, help_text="准备在哪天推进，与承诺的截止日期分开管理。")
    title = models.CharField("任务", max_length=240)
    description = models.TextField("说明", blank=True)
    assignee = models.ForeignKey(Person, null=True, blank=True, on_delete=models.SET_NULL, related_name="tasks")
    planned_start_date = models.DateField("计划开始", null=True, blank=True)
    due_date = models.DateField("截止日期", null=True, blank=True, db_index=True)
    acceptance_date = models.DateField("验收日期", null=True, blank=True)
    status = models.CharField("状态", max_length=24, choices=Status.choices, default=Status.NOT_STARTED, db_index=True)
    priority = models.CharField("优先级", max_length=16, choices=Priority.choices, default=Priority.NORMAL)
    progress = models.PositiveSmallIntegerField("进度", default=0, validators=[MinValueValidator(0), MaxValueValidator(100)])
    current_note = models.TextField("当前说明", blank=True)
    source_meeting = models.ForeignKey(MeetingNote, null=True, blank=True, on_delete=models.SET_NULL, related_name="created_tasks")
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["due_date", "-priority", "title"]
        indexes = [models.Index(fields=["project", "status"]), models.Index(fields=["assignee", "status"])]
        constraints = [models.CheckConstraint(condition=models.Q(progress__gte=0, progress__lte=100), name="task_progress_percentage")]

    @property
    def is_overdue(self):
        return bool(self.due_date and self.due_date < timezone.localdate() and self.status != self.Status.DONE)

    def clean(self):
        if self.phase_id and self.project_id and self.phase.project_id != self.project_id:
            raise ValidationError({"phase": "阶段必须属于当前项目。"})
        if self.planned_start_date and self.due_date and self.planned_start_date > self.due_date:
            raise ValidationError({"due_date": "截止日期不能早于计划开始。"})

    def __str__(self):
        return self.title


class ProgressUpdate(models.Model):
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="progress_updates")
    meeting_note = models.ForeignKey(MeetingNote, null=True, blank=True, on_delete=models.SET_NULL, related_name="progress_updates")
    previous_progress = models.PositiveSmallIntegerField(null=True, blank=True)
    new_progress = models.PositiveSmallIntegerField(null=True, blank=True)
    previous_status = models.CharField(max_length=24, blank=True)
    new_status = models.CharField(max_length=24, blank=True)
    completed_work = models.TextField(blank=True)
    next_step = models.TextField(blank=True)
    recorded_at = models.DateTimeField(default=timezone.now, db_index=True)
    occurred_on = models.DateField(null=True, blank=True, db_index=True)
    snapshot = models.JSONField(default=dict, blank=True)
    applied_to_task = models.BooleanField(default=True)

    class Meta:
        ordering = ["-recorded_at"]


class Risk(TimestampedModel):
    class Type(models.TextChoices):
        RISK = "risk", "风险"
        BLOCKER = "blocker", "阻塞"
        DECISION = "decision", "待决策"
        WARNING = "warning", "预警"
        INCIDENT = "incident", "异常"

    class Status(models.TextChoices):
        OPEN = "open", "待处理"
        TRACKING = "tracking", "跟进中"
        RESOLVED = "resolved", "已解决"
        CLOSED = "closed", "已关闭"

    project = models.ForeignKey(Project, on_delete=models.PROTECT, related_name="risks")
    task = models.ForeignKey(Task, null=True, blank=True, on_delete=models.SET_NULL, related_name="risks")
    risk_type = models.CharField("类型", max_length=16, choices=Type.choices, default=Type.RISK)
    content = models.TextField("内容")
    owner = models.ForeignKey(Person, null=True, blank=True, on_delete=models.SET_NULL, related_name="owned_risks")
    due_date = models.DateField("截止日期", null=True, blank=True)
    status = models.CharField("状态", max_length=16, choices=Status.choices, default=Status.OPEN, db_index=True)
    source_meeting = models.ForeignKey(MeetingNote, null=True, blank=True, on_delete=models.SET_NULL, related_name="risks")
    resolved_at = models.DateTimeField(null=True, blank=True)


class Milestone(TimestampedModel):
    class Status(models.TextChoices):
        NOT_STARTED = "not_started", "未开始"
        IN_PROGRESS = "in_progress", "进行中"
        DONE = "done", "已完成"
        DELAYED = "delayed", "已延期"

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="milestones")
    name = models.CharField("里程碑", max_length=200)
    description = models.TextField("说明", blank=True)
    target_date = models.DateField("目标日期", null=True, blank=True)
    status = models.CharField("状态", max_length=24, choices=Status.choices, default=Status.NOT_STARTED)
    source_meeting = models.ForeignKey(MeetingNote, null=True, blank=True, on_delete=models.SET_NULL, related_name="milestones")

    class Meta:
        ordering = ["target_date", "name"]


class ParseJob(TimestampedModel):
    class Status(models.TextChoices):
        QUEUED = "queued", "等待解析"
        RUNNING = "running", "解析中"
        SUCCESS = "success", "解析完成"
        FAILED = "failed", "解析失败"

    meeting_note = models.OneToOneField(MeetingNote, on_delete=models.CASCADE, related_name="parse_job")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.QUEUED, db_index=True)
    queued_at = models.DateTimeField(default=timezone.now)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    lease_expires_at = models.DateTimeField(null=True, blank=True)
    lease_token = models.UUIDField(null=True, blank=True)
    attempts = models.PositiveIntegerField(default=0)
    error = models.TextField(blank=True)
    draft = models.ForeignKey(ImportDraft, null=True, blank=True, on_delete=models.SET_NULL, related_name="parse_jobs")

    class Meta:
        ordering = ["queued_at", "pk"]
