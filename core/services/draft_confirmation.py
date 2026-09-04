from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from core.models import ImportDraft, MeetingNote, Milestone, Person, ProgressUpdate, Project, Risk, Task
from .llm_schema import ParsedMeeting, normalize_date


class DraftConfirmationError(Exception):
    pass


class DraftAlreadyConfirmed(DraftConfirmationError):
    pass


@dataclass(frozen=True)
class ConfirmationResult:
    created_tasks: int = 0
    updated_tasks: int = 0
    created_risks: int = 0
    created_milestones: int = 0


def _get_optional(model, pk, label):
    if not pk:
        return None
    try:
        return model.objects.get(pk=pk)
    except model.DoesNotExist as exc:
        raise DraftConfirmationError(f"选择的{label}不存在。") from exc


@transaction.atomic
def confirm_draft(draft_id: int, decisions: dict) -> ConfirmationResult:
    try:
        draft = ImportDraft.objects.select_for_update().select_related("meeting_note").get(pk=draft_id)
    except ImportDraft.DoesNotExist as exc:
        raise DraftConfirmationError("解析草稿不存在。") from exc
    if draft.confirmed_at:
        raise DraftAlreadyConfirmed("该草稿已经入库，不能重复确认。")
    parsed = ParsedMeeting.model_validate(draft.payload)
    task_decisions = decisions.get("tasks", [])
    if len(task_decisions) != len(parsed.tasks):
        raise DraftConfirmationError("每条任务都必须选择处理方式。")
    created = updated = 0
    task_by_title = {}
    for item, decision in zip(parsed.tasks, task_decisions, strict=True):
        action = decision.get("action")
        if action == "ignore":
            continue
        project = _get_optional(Project, decision.get("project_id"), "项目")
        if not project:
            raise DraftConfirmationError(f"任务“{item.title}”必须选择项目。")
        assignee = _get_optional(Person, decision.get("assignee_id"), "负责人")
        values = {
            "project": project, "assignee": assignee, "title": item.title,
            "description": item.description,
            "planned_start_date": normalize_date(item.planned_start_date, draft.meeting_note.meeting_date),
            "due_date": normalize_date(item.due_date, draft.meeting_note.meeting_date),
            "acceptance_date": normalize_date(item.acceptance_date, draft.meeting_note.meeting_date),
            "status": item.status, "priority": item.priority, "progress": item.progress,
            "current_note": item.current_note,
        }
        if action == "create":
            task = Task(source_meeting=draft.meeting_note, **values)
            previous_progress, previous_status = None, ""
            created += 1
        elif action == "update":
            task = _get_optional(Task, decision.get("task_id"), "已有任务")
            previous_progress, previous_status = task.progress, task.status
            for key, value in values.items():
                setattr(task, key, value)
            updated += 1
        else:
            raise DraftConfirmationError("任务处理方式无效。")
        task.full_clean()
        task.save()
        ProgressUpdate.objects.create(
            task=task, meeting_note=draft.meeting_note,
            previous_progress=previous_progress, new_progress=task.progress,
            previous_status=previous_status, new_status=task.status,
            completed_work=item.completed_work, next_step=item.next_step,
        )
        task_by_title[item.title] = task
    risk_count = 0
    for index, item in enumerate(parsed.risks):
        decision = (decisions.get("risks") or [{}] * len(parsed.risks))[index]
        if decision.get("action", "ignore") == "ignore":
            continue
        project = _get_optional(Project, decision.get("project_id"), "项目")
        if not project:
            raise DraftConfirmationError("风险必须选择项目。")
        risk = Risk(
            project=project, task=task_by_title.get(item.task_title), risk_type=item.risk_type,
            content=item.content, owner=_get_optional(Person, decision.get("owner_id"), "负责人"),
            due_date=normalize_date(item.due_date, draft.meeting_note.meeting_date), status=item.status,
            source_meeting=draft.meeting_note,
        )
        risk.full_clean(); risk.save(); risk_count += 1
    milestone_count = 0
    for index, item in enumerate(parsed.milestones):
        decision = (decisions.get("milestones") or [{}] * len(parsed.milestones))[index]
        if decision.get("action", "ignore") == "ignore":
            continue
        project = _get_optional(Project, decision.get("project_id"), "项目")
        if not project:
            raise DraftConfirmationError("里程碑必须选择项目。")
        milestone = Milestone(
            project=project, name=item.name, description=item.description,
            target_date=normalize_date(item.target_date, draft.meeting_note.meeting_date),
            status=item.status, source_meeting=draft.meeting_note,
        )
        milestone.full_clean(); milestone.save(); milestone_count += 1
    draft.confirmed_at = timezone.now()
    draft.save(update_fields=["confirmed_at"])
    draft.meeting_note.parse_status = MeetingNote.ParseStatus.IMPORTED
    draft.meeting_note.save(update_fields=["parse_status", "updated_at"])
    return ConfirmationResult(created, updated, risk_count, milestone_count)
