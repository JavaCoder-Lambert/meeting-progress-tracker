from datetime import timedelta

from django.db.models import Count, OuterRef, Q, Subquery
from django.utils import timezone

from core.models import ImportDraft, MeetingNote, Project, Risk, Task


def _task_order(queryset):
    return queryset.order_by("due_date", "project__name", "title", "pk")


def _follow_up_groups(overdue_tasks, due_soon_tasks, stale_tasks):
    task_labels = {}
    ordered_tasks = []
    for label, tasks in (
        ("已逾期", overdue_tasks),
        ("7 天内到期", due_soon_tasks),
        ("长期未更新", stale_tasks),
    ):
        for task in tasks:
            if task.pk in task_labels:
                continue
            task_labels[task.pk] = label
            ordered_tasks.append(task)

    by_person = {}
    for task in ordered_tasks:
        if task.assignee_id:
            by_person.setdefault(task.assignee_id, {"person": task.assignee, "tasks": []})["tasks"].append(task)

    groups = []
    for group in sorted(by_person.values(), key=lambda item: (item["person"].name, item["person"].pk)):
        lines = []
        for index, task in enumerate(group["tasks"], start=1):
            deadline = f"，截止 {task.due_date:%Y-%m-%d}" if task.due_date else ""
            lines.append(f"{index}. 【{task.project.name}】{task.title}（{task_labels[task.pk]}{deadline}）")
        groups.append({
            **group,
            "text": f"{group['person'].name}，以下事项请跟进：\n" + "\n".join(lines),
        })
    return groups


def dashboard_context():
    today = timezone.localdate()
    week_end = today + timedelta(days=7)
    tasks = Task.objects.select_related("project", "assignee")
    open_tasks = tasks.exclude(status=Task.Status.DONE)
    overdue_tasks = _task_order(open_tasks.filter(due_date__lt=today))
    due_soon_tasks = _task_order(open_tasks.filter(due_date__gte=today, due_date__lte=week_end))
    stale_tasks = open_tasks.filter(updated_at__date__lt=today - timedelta(days=7)).order_by(
        "updated_at", "project__name", "title", "pk"
    )
    latest_draft_id = (
        ImportDraft.objects.filter(meeting_note_id=OuterRef("meeting_note_id"))
        .order_by("-created_at", "-pk")
        .values("pk")[:1]
    )
    pending_drafts = ImportDraft.objects.filter(
        pk=Subquery(latest_draft_id),
        confirmed_at__isnull=True,
        meeting_note__parse_status=MeetingNote.ParseStatus.SUCCESS,
    ).select_related("meeting_note").order_by("-meeting_note__meeting_date", "-created_at", "-pk")
    open_risks = Risk.objects.exclude(
        status__in=[Risk.Status.RESOLVED, Risk.Status.CLOSED]
    ).select_related("project", "owner")
    follow_up_groups = _follow_up_groups(overdue_tasks, due_soon_tasks, stale_tasks)
    return {
        "metrics": {
            "in_progress": tasks.filter(status=Task.Status.IN_PROGRESS).count(),
            "acceptance": tasks.filter(status=Task.Status.ACCEPTANCE).count(),
            "delayed": tasks.filter(Q(status=Task.Status.DELAYED) | Q(due_date__lt=today)).exclude(status=Task.Status.DONE).distinct().count(),
            "blocked": tasks.filter(status=Task.Status.BLOCKED).count(),
        },
        "projects": Project.objects.annotate(task_count=Count("tasks"))[:10],
        "due_tasks": due_soon_tasks[:10],
        "risks": open_risks[:8],
        "meetings": MeetingNote.objects.all()[:6],
        "pending_drafts": pending_drafts,
        "overdue_tasks": overdue_tasks,
        "due_soon_tasks": due_soon_tasks,
        "stale_tasks": stale_tasks,
        "open_risks": open_risks,
        "follow_up_groups": follow_up_groups,
        "action_counts": {
            "pending_drafts": pending_drafts.count(),
            "overdue_tasks": overdue_tasks.count(),
            "due_soon_tasks": due_soon_tasks.count(),
            "stale_tasks": stale_tasks.count(),
            "open_risks": open_risks.count(),
        },
    }
