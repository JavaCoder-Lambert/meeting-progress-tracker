"""Signed snapshots for task editors that can remain open across other updates."""
from django.core import signing
from django.core.exceptions import ValidationError
from django.utils.http import url_has_allowed_host_and_scheme

from .ai_history import task_state


class TaskEditConflict(ValidationError):
    pass


def task_edit_baseline(task):
    return signing.dumps(task_state(task), salt="task-edit", compress=True)


def check_task_edit_baseline(task, token, require_current=True):
    """Call with a freshly locked task before validating or applying submitted fields."""
    try:
        baseline = signing.loads(token or "", salt="task-edit")
        if not isinstance(baseline, dict) or baseline.get("id") != task.pk:
            raise signing.BadSignature
    except (signing.BadSignature, TypeError, ValueError) as exc:
        raise TaskEditConflict("任务基线缺失或无效，本次未保存。请保留填写内容并重新核对当前任务。") from exc
    if require_current and baseline != task_state(task):
        raise TaskEditConflict("任务已在其他页面或会议中更新，本次未保存。你的填写已保留，请重新核对当前任务后再保存。")
    return baseline


def safe_task_return(request, fallback):
    destination = request.POST.get("next", request.GET.get("next", ""))
    if destination.startswith("/") and not destination.startswith("//") and url_has_allowed_host_and_scheme(
        destination, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return destination
    return fallback
