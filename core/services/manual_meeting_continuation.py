"""Prepare a fresh meeting from confirmed context, without copying past updates."""
from uuid import uuid4

from django.db.models import Q
from django.utils import timezone

from core.models import Person, Project, Task
from .manual_meetings import ManualMeetingError, task_option


def continuation_tasks(source):
    if not source.confirmed_at:
        raise ManualMeetingError("请先确认本次会议，再准备下次会议。")
    linked_ids = [card.get("task_id") for card in source.state.get("items", []) if card.get("task_id")]
    return Task.objects.filter(
        Q(pk__in=linked_ids) | Q(source_meeting_id=source.meeting_note_id)
        | Q(progress_updates__meeting_note_id=source.meeting_note_id)
    ).exclude(status=Task.Status.DONE).exclude(project__status=Project.Status.ARCHIVED).select_related(
        "project", "assignee"
    ).distinct().order_by("project__name", "title", "pk")


def continuation_initial(source):
    return {
        "title": source.state.get("title", source.meeting_note.title),
        "meeting_date": timezone.localdate(), "meeting_time": "", "agenda": "",
        "projects": list(Project.objects.filter(pk__in=source.state.get("project_ids", [])).values_list("pk", flat=True)),
        "people": list(Person.objects.filter(pk__in=source.state.get("person_ids", [])).values_list("pk", flat=True)),
    }


def continuation_items(source):
    reporters = {card.get("task_id"): card.get("person_id") for card in source.state.get("items", [])
                 if card.get("kind") == "task"}
    return meeting_items(continuation_tasks(source), reporters)


def meeting_items(tasks, reporters=None):
    """Fresh questions, never a copy of earlier completed work or next steps."""
    reporters = reporters or {}
    people = set(Person.objects.filter(pk__in=[pk for pk in reporters.values() if pk]).values_list("pk", flat=True))
    items = []
    for task in tasks:
        option = task_option(task)
        reporter = reporters.get(task.pk)
        items.append({
            "id": str(uuid4()), "kind": "task", "task_id": task.pk, "project_id": task.project_id,
            "person_id": reporter if reporter in people else task.assignee_id,
            "title": task.title, "status": task.status, "progress": task.progress,
            "due_date": option["due_date"], "planned_for": option["planned_for"],
            "baseline": option["baseline"], "recorded": False,
            "completed_work": "", "next_step": "", "content": "",
        })
    return items
