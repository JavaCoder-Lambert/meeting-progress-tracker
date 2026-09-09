from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from core.models import ProgressUpdate, Task
from core.weekplan_models import ProjectWeekPlan, ProjectWeekPlanItem
from .ai_history import task_state
from .meeting_corrections import effective_progress_updates


@transaction.atomic
def create_draft(project, day):
    week = day - timedelta(days=day.weekday())
    existing = ProjectWeekPlan.objects.filter(project=project, week_start=week, status="draft").first()
    if existing:
        return existing
    plans = ProjectWeekPlan.objects.filter(project=project, week_start=week)
    if plans.exists():
        raise ValidationError("本周已有发布计划，请从已发布版本发起修订。")
    return ProjectWeekPlan.objects.create(project=project, week_start=week)


def _claim(plan, version):
    if plan.status != "draft":
        raise ValidationError("已发布计划不可修改，请发起修订。")
    if not ProjectWeekPlan.objects.filter(pk=plan.pk, version=version, status="draft").update(version=version + 1):
        raise ValidationError("草稿版本已变化，请刷新后重试。")
    plan.version = version + 1


@transaction.atomic
def save_draft(pk, version, deliveries):
    plan = ProjectWeekPlan.objects.get(pk=pk)
    tasks = list(Task.objects.filter(pk__in=deliveries, project=plan.project))
    if len(tasks) != len(deliveries):
        raise ValidationError("任务必须属于当前项目。")
    _claim(plan, version)
    plan.items.all().delete()
    ProjectWeekPlanItem.objects.bulk_create([ProjectWeekPlanItem(plan=plan, task=t, delivery=deliveries[t.pk].strip()) for t in tasks])
    return plan


@transaction.atomic
def publish_plan(pk, version):
    plan = ProjectWeekPlan.objects.get(pk=pk)
    if plan.status == "published":
        return plan
    if not plan.items.exists():
        raise ValidationError("空计划不能发布，请先选择交付任务。")
    _claim(plan, version)
    for item in plan.items.select_related("task__assignee", "task__project"):
        if item.task.project_id != plan.project_id:
            raise ValidationError("任务已移出项目，请更新草稿。")
        item.snapshot = task_state(item.task)
        item.snapshot["planned_for"] = item.task.planned_for.isoformat() if item.task.planned_for else None
        item.save(update_fields=["snapshot"])
    plan.status = "published"
    plan.published_at = timezone.now()
    plan.save(update_fields=["status", "published_at"])
    return plan


@transaction.atomic
def save_and_publish_plan(pk, version, deliveries):
    """A retry must match both the submitted draft baseline and frozen contents."""
    plan = ProjectWeekPlan.objects.get(pk=pk)
    normalized = {tid: text.strip() for tid, text in deliveries.items()}
    if plan.status == "published":
        items = list(plan.items.all())
        exact_retry = (bool(items) and plan.version == version + 2
            and {item.task_id: item.delivery for item in items} == normalized
            and all(item.snapshot.get("weekplan_publish_version") == version for item in items))
        if not exact_retry:
            raise ValidationError("计划已由其他页面发布，本次输入未保存；请核对发布内容并发起修订。")
        return plan
    plan = save_draft(pk, version, normalized)
    plan = publish_plan(pk, plan.version)
    for item in plan.items.all():
        item.snapshot["weekplan_publish_version"] = version
        item.save(update_fields=["snapshot"])
    return plan


def review_rows(plan):
    items = list(plan.items.select_related("task__assignee"))
    end = plan.week_start + timedelta(days=6)
    today = timezone.localdate()
    cutoff = min(end, today)
    updates = effective_progress_updates(ProgressUpdate.objects.filter(task_id__in=[i.task_id for i in items]).select_related("meeting_note"))
    latest = {}
    for update in updates:
        day = update.occurred_on or (update.meeting_note.meeting_date if update.meeting_note_id else timezone.localdate(update.recorded_at))
        if plan.week_start > today or day > cutoff:
            continue
        key = (day, update.recorded_at, update.pk)
        if update.task_id not in latest or key > latest[update.task_id][0]:
            latest[update.task_id] = (key, update)
    rows = []
    for item in items:
        update = latest.get(item.task_id, (None, None))[1]
        snap = update.snapshot if update else {}
        complete = (isinstance(snap, dict)
            and all(k in snap for k in ("title", "person_name", "status", "progress", "due_date", "planned_for"))
            and isinstance(snap["title"], str) and bool(snap["title"].strip())
            and snap["status"] in Task.Status.values
            and type(snap["progress"]) is int and 0 <= snap["progress"] <= 100)
        actual = dict(snap, next_step=update.next_step, completed_work=update.completed_work,
            note=snap.get("content") or snap.get("current_note") or "",
            occurred_on=latest[item.task_id][0][0]) if complete else None
        labels = dict(Task.Status.choices)
        rows.append({"item": item, "actual": actual, "done": bool(actual and actual["status"] == Task.Status.DONE),
            "planned_status": labels.get(item.snapshot.get("status"), "未记录"),
            "actual_status": labels.get(actual["status"], "未记录") if actual else "未记录"})
    return rows


@transaction.atomic
def copy_plan(pk, reason, *, carry=False):
    source = ProjectWeekPlan.objects.get(pk=pk)
    if source.status != "published":
        raise ValidationError("请先发布计划。")
    if not carry and not reason.strip():
        raise ValidationError("修订必须填写原因。")
    week = source.week_start + timedelta(days=7 if carry else 0)
    existing = ProjectWeekPlan.objects.filter(project=source.project, week_start=week, status="draft").first()
    if existing:
        if existing.source_id == source.pk:
            return existing
        raise ValidationError("目标周已有草稿，请在已有草稿中添加任务。")
    revisions = ProjectWeekPlan.objects.filter(project=source.project, week_start=week)
    if carry and revisions.exists():
        raise ValidationError("下一周已有发布计划，请修订下一周计划。")
    revision = (revisions.aggregate(value=Max("revision"))["value"] or 0) + 1
    plan = ProjectWeekPlan.objects.create(project=source.project, week_start=week, revision=revision, reason=reason.strip(), source=source)
    for row in review_rows(source):
        if carry and row["done"]:
            continue
        item = row["item"]
        if item.task.project_id == source.project_id:
            ProjectWeekPlanItem.objects.create(plan=plan, task=item.task, delivery=item.delivery)
    return plan
