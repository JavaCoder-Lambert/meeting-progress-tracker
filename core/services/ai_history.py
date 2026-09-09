"""Signed task state retained across AI draft saves and explicit re-review."""
from django.core import signing


class TaskBaselineConflict(ValueError):
    pass


def task_state(task):
    state = {}
    for field in task._meta.concrete_fields:
        value = getattr(task, field.attname)
        state[field.attname] = value.isoformat() if hasattr(value, "isoformat") else value
    state.update(project_name=task.project.name,
                 person_name=task.assignee.name if task.assignee else "")
    return state


def task_baseline(task, draft):
    return signing.dumps({"draft_id": draft.pk, "meeting_date": draft.meeting_note.meeting_date.isoformat(),
                          "task": task_state(task)}, salt="ai-draft-task", compress=True)


def check_task_baseline(task, draft, token, require_current=True):
    try:
        baseline = signing.loads(token or "", salt="ai-draft-task")
        if (baseline["draft_id"] != draft.pk or baseline["task"]["id"] != task.pk
                or baseline["meeting_date"] != draft.meeting_note.meeting_date.isoformat()):
            raise signing.BadSignature
    except (signing.BadSignature, KeyError, TypeError) as exc:
        raise TaskBaselineConflict("任务基线缺失或无效，请保留修改并重新核对当前任务。") from exc
    if require_current and baseline["task"] != task_state(task):
        raise TaskBaselineConflict(f"任务“{task.title}”已被修改，请保留修改并重新核对当前任务。")
    return baseline["task"]
