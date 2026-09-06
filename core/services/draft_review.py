from __future__ import annotations

from django.db.models import Q

from core.models import Person, Project, Task

from .llm_schema import normalize_date
from .task_matching import TaskCandidate, find_task_candidates
from .task_payload import task_review_values


def _date_input(value, meeting_date):
    if value in (None, ""):
        return {"type": "date", "value": ""}
    try:
        normalized = normalize_date(value, meeting_date)
    except (TypeError, ValueError):
        return {"type": "text", "value": str(value)}
    return {"type": "date", "value": normalized.isoformat() if normalized else ""}


def _selected_id(rows, index, key, default_name, defaults):
    if index < len(rows):
        return str(rows[index].get(key) or "")
    match = defaults.get(default_name)
    return str(match.pk) if match else ""


def _incoming_date(value, meeting_date):
    try:
        return normalize_date(value, meeting_date)
    except (TypeError, ValueError):
        return value or None


def _task_diffs(task, item, meeting_date):
    comparisons = (
        ("title", "任务标题", task.title, item.get("title", "")),
        ("assignee", "负责人", task.assignee.name if task.assignee else "", item.get("assignee_name", "")),
        ("description", "任务说明", task.description, item.get("description", "")),
        ("planned_start_date", "计划开始", task.planned_start_date, _incoming_date(item.get("planned_start_date"), meeting_date)),
        ("due_date", "截止日期", task.due_date, _incoming_date(item.get("due_date"), meeting_date)),
        ("acceptance_date", "验收日期", task.acceptance_date, _incoming_date(item.get("acceptance_date"), meeting_date)),
        ("status", "状态", task.status, item.get("status", "")),
        ("priority", "优先级", task.priority, item.get("priority", "")),
        ("progress", "进度", task.progress, item.get("progress")),
        ("current_note", "当前说明", task.current_note, item.get("current_note", "")),
    )
    return [
        {"field": field, "label": label,
         "existing": existing if existing not in (None, "") else "未填写",
         "incoming": incoming if incoming not in (None, "") else "未填写"}
        for field, label, existing, incoming in comparisons
        if existing != incoming
    ]


def _task_recommendation(item, project, person, candidates, candidate_tasks, meeting_date):
    reasons = []
    if not project:
        reasons.append("项目未匹配")
    if item.get("assignee_name") and not person:
        reasons.append("负责人未匹配")
    for date_field in ("planned_start_date", "due_date", "acceptance_date"):
        if _date_input(item.get(date_field), meeting_date)["type"] == "text":
            reasons.append("日期格式异常")
            break

    matching_candidate = None
    if len(candidates) == 1 and candidates[0].score >= 0.9 and project:
        candidate = candidate_tasks[candidates[0].task_id]
        assignee_matches = not item.get("assignee_name") or (
            candidate.assignee and candidate.assignee.name == item["assignee_name"]
        )
        title_matches = candidate.title.strip() == str(item.get("title", "")).strip()
        if candidate.project_id == project.pk and assignee_matches and title_matches:
            matching_candidate = candidate

    if matching_candidate:
        return "update", reasons, matching_candidate
    if candidates:
        reasons.append("疑似重复")
    if reasons:
        return "ignore", reasons, None
    return "create", reasons, None


def build_draft_review(draft, decisions=None, preserve_values=False) -> dict:
    """Prepare compact, safe defaults for rendering an import draft review."""
    payload = draft.payload
    decisions = decisions or {}
    projects = list(Project.objects.all())
    selected_people = {str(row.get(key, "")) for group, key in (("tasks", "assignee_id"), ("risks", "owner_id")) for row in decisions.get(group, [])}
    selected_people = {int(pk) for pk in selected_people if pk.isdecimal() and len(pk) < 12}
    people = list(Person.objects.filter(Q(is_active=True) | Q(pk__in=selected_people)))
    project_by_name = {item.name: item for item in projects}
    person_by_name = {item.name: item for item in people}
    open_tasks = list(Task.objects.exclude(status=Task.Status.DONE).select_related("project", "assignee"))
    open_tasks_by_id = {task.pk: task for task in open_tasks}
    selected_ids = {str(row.get("task_id", "")) for row in decisions.get("tasks", [])}
    selected_ids = {int(pk) for pk in selected_ids if pk.isdecimal() and len(pk) < 12}
    missing_ids = selected_ids.difference(open_tasks_by_id)
    if missing_ids:
        open_tasks_by_id.update({task.pk: task for task in Task.objects.filter(pk__in=missing_ids).select_related("project", "assignee")})

    task_decisions = decisions.get("tasks", [])
    task_rows = []
    for index, item in enumerate(payload.get("tasks", [])):
        decision = task_decisions[index] if index < len(task_decisions) else {}
        project = project_by_name.get(item.get("project_name", ""))
        person = person_by_name.get(item.get("assignee_name", ""))
        candidates = find_task_candidates(item, open_tasks)
        recommended_action, reasons, existing_task = _task_recommendation(
            item, project, person, candidates, open_tasks_by_id, draft.meeting_note.meeting_date
        )
        action = decision.get("action", recommended_action)
        existing_id = str(decision.get("task_id") or "") if "task_id" in decision else str(existing_task.pk if existing_task else "")
        selected_task = open_tasks_by_id.get(int(existing_id)) if existing_id.isdecimal() and len(existing_id) < 12 else None
        if selected_task and not any(candidate.task_id == selected_task.pk for candidate in candidates):
            candidates.append(TaskCandidate(selected_task.pk, selected_task.title, 0))
        if not draft.confirmed_at and not preserve_values:
            item = task_review_values(item, selected_task if action == "update" else None)
        task_rows.append({
            "item": item,
            "candidates": candidates,
            "recommended_action": recommended_action,
            "recommended_existing_id": str(existing_task.pk) if existing_task else "",
            "action": action,
            "existing_id": existing_id,
            "attention_reasons": reasons,
            "needs_attention": bool(reasons),
            "diffs": _task_diffs(selected_task, item, draft.meeting_note.meeting_date) if selected_task else [],
            "project_id": _selected_id(task_decisions, index, "project_id", item.get("project_name", ""), project_by_name),
            "assignee_id": _selected_id(task_decisions, index, "assignee_id", item.get("assignee_name", ""), person_by_name),
            "planned_start_date": _date_input(item.get("planned_start_date"), draft.meeting_note.meeting_date),
            "due_date": _date_input(item.get("due_date"), draft.meeting_note.meeting_date),
            "acceptance_date": _date_input(item.get("acceptance_date"), draft.meeting_note.meeting_date),
        })

    risk_rows = []
    risk_decisions = decisions.get("risks", [])
    for index, item in enumerate(payload.get("risks", [])):
        decision = risk_decisions[index] if index < len(risk_decisions) else {}
        risk_rows.append({
            "item": item,
            "action": decision.get("action", "ignore"),
            "project_id": _selected_id(risk_decisions, index, "project_id", item.get("project_name", ""), project_by_name),
            "owner_id": _selected_id(risk_decisions, index, "owner_id", item.get("owner_name", ""), person_by_name),
            "due_date": _date_input(item.get("due_date"), draft.meeting_note.meeting_date),
        })

    milestone_rows = []
    milestone_decisions = decisions.get("milestones", [])
    for index, item in enumerate(payload.get("milestones", [])):
        decision = milestone_decisions[index] if index < len(milestone_decisions) else {}
        milestone_rows.append({
            "item": item,
            "action": decision.get("action", "ignore"),
            "project_id": _selected_id(milestone_decisions, index, "project_id", item.get("project_name", ""), project_by_name),
            "target_date": _date_input(item.get("target_date"), draft.meeting_note.meeting_date),
        })

    all_actions = [row["action"] for row in [*task_rows, *risk_rows, *milestone_rows]]
    return {
        "task_rows": task_rows,
        "risk_rows": risk_rows,
        "milestone_rows": milestone_rows,
        "review_counts": {
            "create": all_actions.count("create"),
            "update": all_actions.count("update"),
            "ignore": all_actions.count("ignore"),
            "attention": sum(row["needs_attention"] for row in task_rows),
        },
        "projects": projects,
        "people": people,
        "task_statuses": Task.Status.choices,
        "task_priorities": Task.Priority.choices,
    }
