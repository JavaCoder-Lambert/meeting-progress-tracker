from collections.abc import Mapping

from django.db import transaction
from django.utils import timezone

from core.models import ProgressUpdate, Task
from .ai_history import task_state
from .task_editing import check_task_edit_baseline


def sync_task_completion_timestamp(task: Task, previous_status: str) -> None:
    """Keep a task's completion timestamp aligned with its status transition."""
    if previous_status != Task.Status.DONE and task.status == Task.Status.DONE:
        task.completed_at = timezone.now()
    elif previous_status == Task.Status.DONE and task.status != Task.Status.DONE:
        task.completed_at = None


@transaction.atomic
def record_task_progress(task: Task, cleaned_data: Mapping, *, baseline=None) -> ProgressUpdate:
    """Apply one task-progress entry and retain its before/after snapshot."""
    task = Task.objects.select_for_update().get(pk=task.pk)
    if baseline is not None:
        check_task_edit_baseline(task, baseline)
    previous_progress = task.progress
    previous_status = task.status

    if cleaned_data.get("status"):
        task.status = cleaned_data["status"]
    if cleaned_data.get("progress") is not None:
        task.progress = cleaned_data["progress"]
    if "due_date" in cleaned_data:
        task.due_date = cleaned_data["due_date"]
    if "current_note" in cleaned_data:
        task.current_note = cleaned_data["current_note"]

    if task.status == Task.Status.DONE:
        task.progress = 100
    sync_task_completion_timestamp(task, previous_status)

    task.full_clean()
    task.save()
    return ProgressUpdate.objects.create(
        task=task,
        previous_progress=previous_progress,
        new_progress=task.progress,
        previous_status=previous_status,
        new_status=task.status,
        completed_work=cleaned_data.get("completed_work", ""),
        next_step=cleaned_data.get("next_step", ""),
        snapshot=task_state(task),
    )
