"""Single-entry corrections; original progress and meeting text are immutable."""
from copy import deepcopy, copy
from django.core import signing
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone
from core.models import MeetingNote, ProgressUpdate, Task
from core.history_models import MeetingCorrection
from .ai_history import task_state
from .manual_meetings import ManualMeetingError

FIELDS = ("new_progress", "new_status", "completed_work", "next_step")


def update_values(update):
    return {key: getattr(update, key) for key in FIELDS}


def effective_progress_updates(queryset):
    rows = [copy(row) for row in queryset]
    if not rows:
        return rows
    latest = {}
    for correction in MeetingCorrection.objects.filter(progress_update_id__in=[row.pk for row in rows]).order_by("progress_update_id", "-pk"):
        latest.setdefault(correction.progress_update_id, correction)
    for row in rows:
        if row.pk in latest:
            row.applied_correction = latest[row.pk]
            values = latest[row.pk].proposed
            for key in FIELDS:
                setattr(row, key, values[key])
            row.snapshot = deepcopy(row.snapshot or {})
            row.snapshot.update(progress=row.new_progress, status=row.new_status)
    return rows


def correction_baseline(update):
    return signing.dumps({"update": update.pk, "task": task_state(update.task),
        "latest": MeetingCorrection.objects.filter(progress_update=update).values_list("pk", flat=True).first()}, salt="meeting-correction", compress=True)


def correction_request(reason, proposed, apply_current):
    if not isinstance(reason, str) or not reason.strip():
        raise ManualMeetingError("请填写更正原因。")
    if not isinstance(proposed, dict) or set(proposed) - set(FIELDS) or type(apply_current) is not bool:
        raise ManualMeetingError("拟更正字段无效。")
    patch = dict(proposed)
    if "new_progress" in patch and (type(patch["new_progress"]) is not int or not 0 <= patch["new_progress"] <= 100):
        raise ManualMeetingError("进度必须为 0 至 100 的整数。")
    for key in ("completed_work", "next_step"):
        if key in patch:
            if not isinstance(patch[key], str) or len(patch[key]) > 20000:
                raise ManualMeetingError("进展说明过长或格式无效。")
            patch[key] = patch[key].strip()
    if patch.get("new_status") == Task.Status.DONE:
        patch["new_progress"] = 100
    return {"reason":reason.strip(), "proposed":patch, "apply_current":apply_current}


@transaction.atomic
def confirm_correction(update_id, *, reason, proposed, baseline, token, apply_current=False):
    if not isinstance(token, str) or not token or len(token) > 100:
        raise ManualMeetingError("更正标识无效。")
    request = correction_request(reason, proposed, apply_current)
    # Request identity never depends on the now-changing effective/current state.
    MeetingNote.objects.filter(progress_updates__pk=update_id).update(id=F("id"))
    existing = MeetingCorrection.objects.filter(idempotency_key=token).first()
    if existing:
        if existing.progress_update_id != update_id:
            raise ManualMeetingError("更正标识已用于其他条目。", conflict=True)
        if existing.original.get("_request") != request:
            raise ManualMeetingError("同一更正标识的内容已变化，请重新发起更正。", conflict=True)
        return existing
    update = ProgressUpdate.objects.select_related("task__project", "task__assignee", "meeting_note").get(pk=update_id)
    if not update.meeting_note_id:
        raise ManualMeetingError("仅支持更正会议产生的进展。")
    if not isinstance(reason, str) or not reason.strip():
        raise ManualMeetingError("请填写更正原因。")
    try:
        data = signing.loads(baseline, salt="meeting-correction")
        if data["update"] != update.pk:
            raise signing.BadSignature
    except (signing.BadSignature, KeyError, TypeError) as exc:
        raise ManualMeetingError("更正基线无效，请重新核对。", conflict=True) from exc
    latest = MeetingCorrection.objects.filter(progress_update=update).values_list("pk", flat=True).first()
    if data["latest"] != latest:
        raise ManualMeetingError("此条进展已有更正，请重新核对。", conflict=True)
    values = update_values(effective_progress_updates([update])[0])
    if not isinstance(proposed, dict) or set(proposed) - set(FIELDS):
        raise ManualMeetingError("拟更正字段无效。")
    values.update(request["proposed"])
    if type(values["new_progress"]) is not int or not 0 <= values["new_progress"] <= 100 or values["new_status"] not in Task.Status.values:
        raise ManualMeetingError("请选择状态，并填写 0 至 100 的整数进度。")
    for key in ("completed_work", "next_step"):
        if not isinstance(values[key], str) or len(values[key]) > 20000:
            raise ManualMeetingError("进展说明过长或格式无效。")
    if values["new_status"] == Task.Status.DONE:
        values["new_progress"] = 100
    task = update.task
    current = task_state(task)
    if apply_current:
        business_date = update.occurred_on or update.meeting_note.meeting_date
        if business_date != timezone.localdate() or not update.applied_to_task:
            raise ManualMeetingError("历史更正只修正报告引用值，不更新当前任务。")
        later = ProgressUpdate.objects.filter(task=task).filter(Q(recorded_at__gt=update.recorded_at) | Q(recorded_at=update.recorded_at, pk__gt=update.pk)).exists()
        if later or current != data["task"]:
            raise ManualMeetingError("任务已有后续进展或修改，不能覆盖；请重新核对或仅更正历史。", conflict=True)
        task.progress, task.status = values["new_progress"], values["new_status"]
        task.current_note = "\n".join(filter(None, [values["completed_work"], values["next_step"]]))
        if task.status == Task.Status.DONE:
            task.completed_at = task.completed_at or timezone.now()
        else:
            task.completed_at = None
        task.full_clean(); task.save()
    return MeetingCorrection.objects.create(meeting_note=update.meeting_note, progress_update=update,
        reason=reason.strip(), original={**update_values(update), "_request":request}, current=current, proposed=values,
        applied_to_task=apply_current, idempotency_key=token)
