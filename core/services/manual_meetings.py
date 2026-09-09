"""Durable manual meeting drafts and their explicit confirmation boundary."""
from copy import deepcopy
from datetime import date, datetime, time

from django.core import signing
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from core.models import MeetingNote, MeetingSession, ProgressUpdate, Task


class ManualMeetingError(ValueError):
    def __init__(self, message, conflict=False):
        super().__init__(message)
        self.conflict = conflict


def task_option(task):
    data = {"id": task.pk, "title": task.title, "project_id": task.project_id,
            "project_name": task.project.name, "assignee_id": task.assignee_id,
            "assignee_name": task.assignee.name if task.assignee else "",
            "status": task.status, "status_label": task.get_status_display(), "progress": task.progress,
            "due_date": task.due_date.isoformat() if task.due_date else "",
            "planned_for": task.planned_for.isoformat() if task.planned_for else "",
            "current_note": task.current_note}
    data["baseline"] = signing.dumps({**data, "updated_at": task.updated_at.isoformat(),
        "planned_start_date": task.planned_start_date.isoformat() if task.planned_start_date else ""}, salt="manual-meeting-task")
    return data


@transaction.atomic
def create_session(state):
    from .manual_meeting_state import normalize_state
    state = normalize_state(state)
    note = MeetingNote.objects.create(title=state["title"], meeting_date=state["meeting_date"], raw_text="")
    return MeetingSession.objects.create(meeting_note=note, state=state)


@transaction.atomic
def save_session(pk, version, state):
    from .manual_meeting_state import check_references, normalize_state
    state = normalize_state(state, check_refs=False)
    version = _version(version)
    count = MeetingSession.objects.filter(pk=pk, version=version, confirmed_at__isnull=True).update(
        version=version + 1, state=state, updated_at=timezone.now())
    if not count:
        # A committed save may lose its response. Only the immediately following
        # revision with the same normalized content can acknowledge that retry.
        saved = MeetingSession.objects.select_related("meeting_note").filter(
            pk=pk, version=version + 1, confirmed_at__isnull=True).first()
        if saved is not None and saved.state == state:
            return saved
        raise ManualMeetingError("草稿已更新或已确认，请保留当前内容并重新打开。", conflict=True)
    check_references(state)
    session = MeetingSession.objects.select_related("meeting_note").get(pk=pk)
    MeetingNote.objects.filter(pk=session.meeting_note_id).update(title=state["title"], meeting_date=state["meeting_date"], updated_at=timezone.now())
    return session


def _version(value):
    if isinstance(value, bool) or not isinstance(value, (int, str)) or not str(value).isascii() or not str(value).isdigit():
        raise ManualMeetingError("草稿版本无效。")
    if len(str(value)) > 10 or int(value) > 2147483646:
        raise ManualMeetingError("草稿版本无效。")
    return int(value)


def preview_session(session):
    from .manual_meeting_preview import build_preview
    return build_preview(session)[0]


@transaction.atomic
def confirm_session(pk, version):
    from .manual_meeting_preview import build_preview
    version = _version(version)
    # Acquire SQLite's write lock before reading any task or session state.
    acquired = MeetingSession.objects.filter(pk=pk, version=version, confirmed_at__isnull=True).update(
        version=F("version") + 1, updated_at=timezone.now())
    try:
        session = MeetingSession.objects.select_related("meeting_note").get(pk=pk)
    except MeetingSession.DoesNotExist as exc:
        raise ManualMeetingError("会议不存在。") from exc
    if session.confirmed_at:
        return session
    if not acquired:
        raise ManualMeetingError("草稿已在其他页面更新，请重新核对。", conflict=True)
    preview, records = build_preview(session)
    if preview["errors"]:
        raise ManualMeetingError("；".join(preview["errors"]), conflict=any(row.get("conflict") for row in records))
    business_date = date.fromisoformat(session.state["meeting_date"])
    business_time = time.fromisoformat(session.state["meeting_time"]) if session.state.get("meeting_time") else timezone.localtime().time().replace(tzinfo=None)
    completed_at = timezone.make_aware(datetime.combine(business_date, business_time))
    for row in records:
        if row["card"]["kind"] != "task":
            model = row["model"]
            if model is not None:
                if isinstance(model, Task) and model.status == Task.Status.DONE:
                    model.completed_at = completed_at
                model.save()
                if isinstance(model, Task):
                    ProgressUpdate.objects.create(
                        task=model, meeting_note=session.meeting_note, new_progress=model.progress,
                        new_status=model.status, completed_work=row["card"]["completed_work"],
                        next_step=row["card"]["next_step"], occurred_on=business_date,
                        applied_to_task=True, snapshot={**row["snapshot"], "project_id": model.project_id,
                            "person_id": model.assignee_id, "status": model.status, "progress": model.progress,
                            "due_date": row["card"]["due_date"], "planned_for": row["card"]["planned_for"]},
                    )
            continue
        task, card, baseline = row["task"], row["card"], row["baseline"]
        if not preview["historical"]:
            if baseline["status"] != Task.Status.DONE and task.status == Task.Status.DONE:
                task.completed_at = completed_at
            elif task.status != Task.Status.DONE:
                task.completed_at = None
            task.save()
        ProgressUpdate.objects.create(
            task=task, meeting_note=session.meeting_note, previous_progress=baseline["progress"],
            new_progress=task.progress, previous_status=baseline["status"], new_status=task.status,
            completed_work=card.get("completed_work", ""), next_step=card.get("next_step", ""),
            occurred_on=business_date, applied_to_task=not preview["historical"], snapshot=row["snapshot"],
        )
    from .risk_followups import confirm_followup
    for followup in session.state.get("risk_followups", []):
        confirm_followup(meeting_note=session.meeting_note, **followup)
    session.confirmed_at = timezone.now()
    session.minutes = preview["minutes"]
    session.save(update_fields=["confirmed_at", "minutes", "updated_at"])
    session.meeting_note.raw_text = session.minutes
    session.meeting_note.save(update_fields=["raw_text", "updated_at"])
    return session


def serialize_session(session):
    from .manual_meeting_state import baseline_values
    state = deepcopy(session.state)
    for card in state.get("items", []):
        card["baseline_values"] = baseline_values(card)
    return {"id": session.pk, "note_id": session.meeting_note_id, "version": session.version,
            "state": state, "confirmed": session.confirmed_at is not None,
            "updated_at": session.updated_at.isoformat(), "minutes": session.minutes}
