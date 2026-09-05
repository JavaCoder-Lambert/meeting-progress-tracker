from collections.abc import Mapping

from django.db import transaction
from django.utils import timezone

from core.models import ProgressUpdate, Task


def sync_task_completion_timestamp(task: Task, previous_status: str) -> None:
    """Keep a task's completion timestamp aligned with its status transition."""
    if previous_status != Task.Status.DONE and task.status == Task.Status.DONE:
        task.completed_at = timezone.now()
    elif previous_status == Task.Status.DONE and task.status != Task.Status.DONE:
        task.completed_at = None


@transaction.atomic
def record_task_progress(task: Task, cleaned_data: Mapping) -> ProgressUpdate:
    """Apply one task-progress entry and retain its before/after snapshot."""
    task = Task.objects.select_for_update().get(pk=task.pk)
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
    )
