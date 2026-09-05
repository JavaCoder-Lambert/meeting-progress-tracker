from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from core.models import ImportDraft, MeetingNote, Milestone, Person, ProgressUpdate, Project, Risk, Task
from .llm_schema import ParsedMeeting, normalize_date
from .task_payload import TASK_DEFAULTS, task_provided_fields
from .progress_updates import sync_task_completion_timestamp


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


def _validate_instance(instance, label):
    try:
        instance.full_clean()
    except ValidationError as exc:
        raise DraftConfirmationError(f"{label}信息有误：{'；'.join(exc.messages)}") from exc


@transaction.atomic
def confirm_draft(draft_id: int, decisions: dict, payload: dict | None = None) -> ConfirmationResult:
    try:
        draft = ImportDraft.objects.select_for_update().select_related("meeting_note").get(pk=draft_id)
    except ImportDraft.DoesNotExist as exc:
        raise DraftConfirmationError("解析草稿不存在。") from exc
    if draft.confirmed_at:
        raise DraftAlreadyConfirmed("该草稿已经入库，不能重复确认。")

    meeting_drafts = list(
        ImportDraft.objects.select_for_update()
        .filter(meeting_note_id=draft.meeting_note_id)
        .only("id", "created_at", "confirmed_at")
        .order_by("-created_at", "-pk")
    )
    if any(item.confirmed_at for item in meeting_drafts if item.pk != draft.pk):
        raise DraftAlreadyConfirmed("该会议已经入库过一份草稿，不能重复导入。")
    if meeting_drafts and meeting_drafts[0].pk != draft.pk:
        raise DraftConfirmationError("该草稿已被较新的解析结果替代，请使用最新草稿。")
    if draft.meeting_note.parse_status != MeetingNote.ParseStatus.SUCCESS:
        raise DraftConfirmationError("会议必须处于解析成功状态才能确认，请返回会议详情查看当前状态。")

    source_payload = payload if payload is not None else draft.payload
    parsed = ParsedMeeting.model_validate(source_payload)
    task_decisions = decisions.get("tasks", [])
    if len(task_decisions) != len(parsed.tasks):
        raise DraftConfirmationError("每条任务都必须选择处理方式。")
    risk_decisions = decisions.get("risks", [])
    if len(risk_decisions) != len(parsed.risks):
        raise DraftConfirmationError("每条风险都必须选择处理方式。")
    milestone_decisions = decisions.get("milestones", [])
    if len(milestone_decisions) != len(parsed.milestones):
        raise DraftConfirmationError("每条里程碑都必须选择处理方式。")
    for rows, allowed, label in (
        (task_decisions, {"create", "update", "ignore"}, "任务"),
        (risk_decisions, {"create", "ignore"}, "风险"),
        (milestone_decisions, {"create", "ignore"}, "里程碑"),
    ):
        if any(row.get("action") not in allowed for row in rows):
            raise DraftConfirmationError(f"{label}处理方式缺失或无效，请逐项选择。")
    created = updated = 0
    task_by_title = {}
    for item, source_item, decision in zip(parsed.tasks, source_payload.get("tasks", []), task_decisions, strict=True):
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
            if not task:
                raise DraftConfirmationError(f"任务“{item.title}”选择更新时必须选择已有任务。")
            if task.risks.exclude(project=project).exists():
                raise DraftConfirmationError(f"任务“{item.title}”仍关联其他项目的风险，请先调整关联后再移动任务。")
            if task.phase_id and task.phase.project_id != project.pk:
                raise DraftConfirmationError(f"任务“{item.title}”仍关联原项目阶段，请先调整所属阶段后再移动任务。")
            previous_progress, previous_status = task.progress, task.status
            preserve_when_empty = {
                "assignee", "description", "planned_start_date", "due_date",
                "acceptance_date", "current_note",
            }
            for key, value in values.items():
                if key in TASK_DEFAULTS and key not in task_provided_fields(source_item):
                    continue
                if key in preserve_when_empty and value in (None, ""):
                    continue
                setattr(task, key, value)
            updated += 1
        else:
            raise DraftConfirmationError("任务处理方式无效。")
        sync_task_completion_timestamp(task, previous_status)
        _validate_instance(task, "任务")
        task.save()
        for field in TASK_DEFAULTS:
            setattr(item, field, getattr(task, field))
        ProgressUpdate.objects.create(
            task=task, meeting_note=draft.meeting_note,
            previous_progress=previous_progress, new_progress=task.progress,
            previous_status=previous_status, new_status=task.status,
            completed_work=item.completed_work, next_step=item.next_step,
        )
        task_by_title[item.title] = task
    risk_count = 0
    for item, decision in zip(parsed.risks, risk_decisions, strict=True):
        if decision["action"] == "ignore":
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
        if risk.task and risk.task.project_id != risk.project_id:
            raise DraftConfirmationError("风险关联任务必须属于同一项目，请先调整项目或任务关联。")
        _validate_instance(risk, "风险")
        risk.save(); risk_count += 1
    milestone_count = 0
    for item, decision in zip(parsed.milestones, milestone_decisions, strict=True):
        if decision["action"] == "ignore":
            continue
        project = _get_optional(Project, decision.get("project_id"), "项目")
        if not project:
            raise DraftConfirmationError("里程碑必须选择项目。")
        milestone = Milestone(
            project=project, name=item.name, description=item.description,
            target_date=normalize_date(item.target_date, draft.meeting_note.meeting_date),
            status=item.status, source_meeting=draft.meeting_note,
        )
        _validate_instance(milestone, "里程碑")
        milestone.save(); milestone_count += 1
    draft.payload = parsed.model_dump(mode="json")
    draft.confirmed_at = timezone.now()
    draft.save(update_fields=["payload", "confirmed_at"])
    draft.meeting_note.parse_status = MeetingNote.ParseStatus.IMPORTED
    draft.meeting_note.save(update_fields=["parse_status", "updated_at"])
    return ConfirmationResult(created, updated, risk_count, milestone_count)
