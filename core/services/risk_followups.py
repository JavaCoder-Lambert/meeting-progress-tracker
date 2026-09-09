"""Risk followup draft preparation and append-only confirmation."""
from datetime import date
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from core.models import Risk
from core.history_models import RiskFollowup
from .manual_meetings import ManualMeetingError


def risk_state(risk):
    return {"id": risk.pk, "project_id": risk.project_id, "project_name": risk.project.name,
        "content": risk.content, "risk_type": risk.risk_type, "task_id": risk.task_id,
        "status": risk.status, "status_label": risk.get_status_display(), "owner_id": risk.owner_id,
        "owner_name": risk.owner.name if risk.owner_id else "",
        "due_date": risk.due_date.isoformat() if risk.due_date else "",
        "updated_at": risk.updated_at.isoformat()}


def risk_baseline(risk):
    return signing.dumps(risk_state(risk), salt="risk-followup", compress=True)


def prepare_followup(risk_id, meeting_note, *, response, next_step, reason, status, owner_id, due_date, baseline, token):
    risk = Risk.objects.select_related("project", "owner").get(pk=risk_id)
    if not isinstance(token, str) or not token or len(token) > 100:
        raise ManualMeetingError("跟进标识无效。")
    try:
        before = signing.loads(baseline, salt="risk-followup")
        if before["id"] != risk.pk:
            raise signing.BadSignature
    except (signing.BadSignature, TypeError, KeyError) as exc:
        raise ManualMeetingError("风险基线无效，请重新选择。", conflict=True) from exc
    business_date = date.fromisoformat(meeting_note.meeting_date) if isinstance(meeting_note.meeting_date, str) else meeting_note.meeting_date
    historical = business_date < timezone.localdate()
    if business_date > timezone.localdate():
        raise ManualMeetingError("未来会议不能提前确认。")
    if not historical and before != risk_state(risk):
        raise ManualMeetingError("风险已被其他页面修改，请重新核对。", conflict=True)
    for value in (response, next_step, reason):
        if not isinstance(value, str) or len(value) > 20000:
            raise ManualMeetingError("跟进说明格式无效或过长。")
    if not response.strip():
        raise ManualMeetingError("请填写本次答复。")
    if status not in Risk.Status.values:
        raise ManualMeetingError("风险状态无效。")
    terminal = {Risk.Status.CLOSED, Risk.Status.RESOLVED}
    if (status != before["status"] and (status in terminal or before["status"] in terminal)) and not reason.strip():
        raise ManualMeetingError("关闭或重新打开风险需要填写理由。")
    try:
        due = date.fromisoformat(due_date) if due_date else None
    except (ValueError, TypeError) as exc:
        raise ManualMeetingError("目标日期无效。") from exc
    risk.status, risk.owner_id, risk.due_date = status, owner_id, due
    try:
        risk.full_clean()
    except ValidationError as exc:
        raise ManualMeetingError("；".join(exc.messages)) from exc
    after = risk_state(risk)
    return risk, before, after, historical


@transaction.atomic
def confirm_followup(risk_id, meeting_note, **data):
    request = {}
    for key in ("response", "next_step", "reason", "status", "due_date"):
        value = data.get(key)
        if not isinstance(value, str):
            raise ManualMeetingError("跟进内容格式无效。")
        request[key] = value.strip()
    request["owner_id"] = data.get("owner_id")
    data = {**data, **request}
    request["occurred_on"] = str(meeting_note.meeting_date)
    Risk.objects.filter(pk=risk_id).update(id=F("id"))
    existing = RiskFollowup.objects.filter(idempotency_key=data.get("token")).first()
    if existing:
        if existing.risk_id != risk_id or existing.meeting_note_id != meeting_note.pk:
            raise ManualMeetingError("跟进标识已用于其他事项。", conflict=True)
        if existing.before.get("_request") != request:
            raise ManualMeetingError("同一跟进标识的内容已变化，请重新记录。", conflict=True)
        return existing
    risk, before, after, historical = prepare_followup(risk_id, meeting_note, **data)
    if not historical:
        risk.resolved_at = timezone.now() if risk.status in {Risk.Status.CLOSED, Risk.Status.RESOLVED} else None
        risk.save()
        after = risk_state(risk)
    return RiskFollowup.objects.create(risk=risk, meeting_note=meeting_note, occurred_on=meeting_note.meeting_date,
        before={**before, "_request":request}, after=after, response=data["response"].strip(), next_step=data["next_step"].strip(),
        reason=data["reason"].strip(), applied_to_risk=not historical, idempotency_key=data["token"])
