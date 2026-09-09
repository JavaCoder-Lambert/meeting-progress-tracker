from django.db import models


class ProjectWeekPlan(models.Model):
    project = models.ForeignKey("core.Project", on_delete=models.PROTECT, related_name="weekplans")
    week_start = models.DateField()
    revision = models.PositiveIntegerField(default=1)
    version = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=16, choices=[("draft", "草稿"), ("published", "已发布")], default="draft")
    reason = models.TextField(blank=True)
    published_at = models.DateTimeField(null=True, blank=True)
    source = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="copies")

    class Meta:
        ordering = ["-week_start", "-revision"]
        constraints = [models.UniqueConstraint(fields=["project", "week_start", "revision"], name="weekplan_revision_unique"), models.UniqueConstraint(fields=["project", "week_start"], condition=models.Q(status="draft"), name="weekplan_one_draft")]
        indexes = [models.Index(fields=["project", "week_start"], name="weekplan_project_week")]


class ProjectWeekPlanItem(models.Model):
    plan = models.ForeignKey(ProjectWeekPlan, on_delete=models.CASCADE, related_name="items")
    task = models.ForeignKey("core.Task", on_delete=models.PROTECT, related_name="weekplan_items")
    delivery = models.TextField(blank=True)
    snapshot = models.JSONField(default=dict)

    class Meta:
        ordering = ["pk"]
        constraints = [models.UniqueConstraint(fields=["plan", "task"], name="weekplan_item_unique")]
