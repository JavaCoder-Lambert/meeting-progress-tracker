from dataclasses import dataclass
from datetime import datetime

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from core.models import ImportDraft, MeetingNote, Milestone, Person, ProgressUpdate, Project, ProjectPhase, Risk, Task
from .ai_history import TaskBaselineConflict, check_task_baseline
from .llm_schema import ParsedMeeting, normalize_date
from .task_payload import TASK_DEFAULTS, task_provided_fields
from .progress_updates import sync_task_completion_timestamp


class DraftConfirmationError(Exception):
    pass


class DraftAlreadyConfirmed(DraftConfirmationError):
    pass


class DraftTaskConflict(DraftConfirmationError):
    pass


@dataclass(frozen=True)
class ConfirmationResult:
    created_tasks: int = 0
    updated_tasks: int = 0
    created_risks: int = 0
    created_milestones: int = 0
    historical_updates: int = 0


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
    business_date = draft.meeting_note.meeting_date
    today = timezone.localdate()
    if business_date > today:
        raise DraftConfirmationError("未来会议可以暂存，但不能提前确认。")
    historical = business_date < today

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
    update_targets = set()
    for decision in task_decisions:
        if decision.get("action") != "update":
            continue
        try:
            target_id = int(decision.get("task_id"))
        except (TypeError, ValueError):
            continue  # The normal item validation explains missing/invalid selections.
        if target_id in update_targets:
            raise DraftConfirmationError("同一任务不能重复更新，请合并记录或忽略重复项。")
        update_targets.add(target_id)
    created = updated = 0
    task_by_title = {}
    for item, source_item, decision in zip(parsed.tasks, source_payload.get("tasks", []), task_decisions, strict=True):
        action = decision.get("action")
        if action == "ignore":
            continue
        historical_person = {}
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
            if not historical or decision.get("task_baseline"):
                try:
                    baseline = check_task_baseline(task, draft, decision.get("task_baseline"),
                                                   require_current=not historical)
                except TaskBaselineConflict as exc:
                    raise DraftTaskConflict(str(exc)) from exc
                if historical:
                    # Missing AI fields retain what was reviewed, even if the live task moved on.
                    for field in ("description", "planned_start_date", "due_date", "acceptance_date",
                                  "status", "priority", "progress", "current_note", "planned_for", "completed_at"):
                        setattr(task, field, Task._meta.get_field(field).to_python(baseline[field]))
                    if assignee is None:
                        historical_person = {"person_id": baseline["assignee_id"],
                                             "person_name": baseline["person_name"]}
            if historical:
                # This instance validates an archive snapshot; today's phase is not being moved.
                task.phase = None
            else:
                if task.risks.exclude(project=project).exists():
                    raise DraftConfirmationError(f"任务“{item.title}”仍关联其他项目的风险，请先调整关联后再移动任务。")
                if (task.phase_id and task.phase.project_id != project.pk
                        and "phase_id" not in decision.get("planning_provided", [])):
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
        planning = decision.get("planning", {})
        provided_planning = decision.get("planning_provided", [])
        if "phase_id" in provided_planning:
            try:
                task.phase = _get_optional(ProjectPhase, planning.get("phase_id"), "阶段")
            except (ValueError, TypeError) as exc:
                raise DraftConfirmationError("所属阶段无效，请重新选择。") from exc
        if "planned_for" in provided_planning:
            try:
                task.planned_for = normalize_date(planning.get("planned_for"), business_date)
            except (ValueError, TypeError) as exc:
                raise DraftConfirmationError("安排日期无效，请使用 YYYY-MM-DD。") from exc
        sync_task_completion_timestamp(task, previous_status)
        if task.status == Task.Status.DONE and previous_status != Task.Status.DONE:
            task.completed_at = timezone.make_aware(datetime.combine(
                business_date, timezone.localtime().time().replace(tzinfo=None)))
        _validate_instance(task, "任务")
        applied_to_task = action == "create" or not historical
        if applied_to_task:
            task.save()
        source_item["_planning"] = {"phase_id": str(task.phase_id or ""),
                                    "planned_for": task.planned_for.isoformat() if task.planned_for else ""}
        for field in TASK_DEFAULTS:
            setattr(item, field, getattr(task, field))
        ProgressUpdate.objects.create(
            task=task, meeting_note=draft.meeting_note,
            previous_progress=previous_progress, new_progress=task.progress,
            previous_status=previous_status, new_status=task.status,
            completed_work=item.completed_work, next_step=item.next_step,
            occurred_on=business_date, applied_to_task=applied_to_task,
            snapshot={
                "title": task.title, "project_id": task.project_id, "project_name": task.project.name,
                "person_id": task.assignee_id, "person_name": task.assignee.name if task.assignee else "",
                "status": task.status, "progress": task.progress, "content": task.current_note,
                "due_date": task.due_date.isoformat() if task.due_date else "",
                "planned_for": task.planned_for.isoformat() if task.planned_for else "",
                **historical_person,
            },
        )
        task_by_title[item.title] = task.pk
    risk_count = 0
    for item, decision in zip(parsed.risks, risk_decisions, strict=True):
        if decision["action"] == "ignore":
            continue
        project = _get_optional(Project, decision.get("project_id"), "项目")
        if not project:
            raise DraftConfirmationError("风险必须选择项目。")
        risk = Risk(
            project=project, task=_get_optional(Task, task_by_title.get(item.task_title), "关联任务"), risk_type=item.risk_type,
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
    for item, source_item in zip(draft.payload.get("tasks", []), source_payload.get("tasks", []), strict=True):
        if "_planning" in source_item:
            item["_planning"] = source_item["_planning"]
    draft.confirmed_at = timezone.now()
    draft.save(update_fields=["payload", "confirmed_at"])
    draft.meeting_note.parse_status = MeetingNote.ParseStatus.IMPORTED
    draft.meeting_note.save(update_fields=["parse_status", "updated_at"])
    return ConfirmationResult(created, 0 if historical else updated, risk_count, milestone_count,
                              updated if historical else 0)
