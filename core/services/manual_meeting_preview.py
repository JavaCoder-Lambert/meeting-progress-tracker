"""Prepare the exact confirmation effects without saving business records."""
from datetime import date

from django.core import signing
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.utils import timezone

from core.models import Person, Project, Risk, Task
from .manual_meetings import ManualMeetingError, task_option
from .manual_meeting_state import normalize_state


def build_preview(session):
    preview = {"items": [], "errors": [], "historical": False,
               "counts": {"tasks": 0, "new_tasks": 0, "risks": 0, "notes": 0}, "minutes": ""}
    try:
        state = normalize_state(session.state)
    except ManualMeetingError as exc:
        preview["errors"].append(str(exc))
        return preview, []
    historical = date.fromisoformat(state["meeting_date"]) < timezone.localdate()
    preview["historical"] = historical
    if date.fromisoformat(state["meeting_date"]) > timezone.localdate():
        preview["errors"].append("未来会议可以暂存，但不能提前确认。")
    records = []
    minutes = [f'# {state["title"]}', f'日期：{state["meeting_date"]} {state.get("meeting_time", "")}', state.get("agenda", "")]
    for card in state["items"]:
        if not card.get("recorded"):
            continue
        if card["kind"] != "task":
            try:
                row, summary, snapshot = prepare_other(card, session)
            except (ManualMeetingError, ValidationError, ObjectDoesNotExist) as exc:
                preview["errors"].append(str(exc))
                continue
            records.append(row)
            count = {"new_task": "new_tasks", "risk": "risks", "decision": "risks", "note": "notes"}[card["kind"]]
            preview["counts"][count] += 1
            preview["items"].append({"id": card["id"], "kind": card["kind"], **snapshot, "summary": summary, "historical": False})
            minutes.append(f'\n## {snapshot["title"]}\n{snapshot["project_name"]} / {snapshot["person_name"]}\n{summary}')
            continue
        try:
            row, summary = prepare_task(card, historical)
        except (ManualMeetingError, ValidationError, ObjectDoesNotExist) as exc:
            preview["errors"].append("；".join(exc.messages) if isinstance(exc, ValidationError) else str(exc))
            records.append({"conflict": getattr(exc, "conflict", False)})
            continue
        snapshot = row["snapshot"]
        records.append(row)
        preview["items"].append({"id": card["id"], "kind": "task", **snapshot, "summary": summary, "historical": historical})
        preview["counts"]["tasks"] += 1
        minutes.append(f'\n## {snapshot["title"]}\n{snapshot["project_name"]} / {snapshot["person_name"]}\n{summary}')
    from .risk_followups import prepare_followup
    for followup in state.get("risk_followups", []):
        try:
            risk, before, after, historical_risk = prepare_followup(meeting_note=session.meeting_note, **followup)
        except (ManualMeetingError, ObjectDoesNotExist) as exc:
            preview["errors"].append(str(exc))
            records.append({"conflict": getattr(exc, "conflict", False)})
            continue
        summary = f'本次答复：{followup["response"]}\n下一步：{followup["next_step"]}\n状态：{dict(Risk.Status.choices)[before["status"]]} → {risk.get_status_display()}\n负责人：{before.get("owner_name") or "未指定"} → {after.get("owner_name") or "未指定"}\n目标日期：{before.get("due_date") or "未设置"} → {after.get("due_date") or "未设置"}\n理由：{followup["reason"] or "无"}'
        if historical_risk:
            summary += "\n仅追加历史，不修改当前风险"
        preview["items"].append({"kind":"risk", "title":f"跟进已有风险：{risk.content}", "project_name":risk.project.name,
            "person_name":risk.owner.name if risk.owner else "", "summary":summary, "historical":historical_risk})
        preview["counts"]["risks"] += 1
        minutes.append(f'\n## 跟进风险：{risk.content}\n{summary}')
    preview["minutes"] = "\n".join(minutes).strip()
    return preview, records


def prepare_task(card, historical):
    try:
        task = Task.objects.select_related("project", "assignee").get(pk=card["task_id"])
    except Task.DoesNotExist as exc:
        raise ManualMeetingError("关联任务不存在，请重新选择。") from exc
    try:
        baseline = signing.loads(card["baseline"], salt="manual-meeting-task")
        if not isinstance(baseline, dict) or baseline.get("id") != task.pk:
            raise signing.BadSignature
    except signing.BadSignature as exc:
        raise ManualMeetingError("任务基线无效或不匹配，请重新选择任务。", conflict=True) from exc
    current = signing.loads(task_option(task)["baseline"], salt="manual-meeting-task")
    if not historical and baseline != current:
        raise ManualMeetingError(f'任务「{task.title}」已被修改，请重新选择最新任务。', conflict=True)
    task.status = card.get("status") or baseline["status"]
    task.progress = card.get("progress") if card.get("progress") not in (None, "") else baseline["progress"]
    if task.status == Task.Status.DONE:
        task.progress = 100
    for field in ("due_date", "planned_for"):
        value = card.get(field, baseline[field])
        setattr(task, field, date.fromisoformat(value) if value else None)
    task.current_note = "\n".join(filter(None, [card.get("completed_work"), card.get("next_step"), card.get("content")])) or baseline["current_note"]
    if historical:
        task.planned_start_date = date.fromisoformat(baseline["planned_start_date"]) if baseline.get("planned_start_date") else None
    task.full_clean()
    person = Person.objects.get(pk=card["person_id"]) if card.get("person_id") else None
    snapshot = {"title": baseline["title"], "project_name": baseline["project_name"],
                "person_name": person.name if person else baseline["assignee_name"],
                "project_id": baseline["project_id"], "person_id": card.get("person_id") or baseline["assignee_id"],
                "status": task.status, "progress": task.progress, "content": card.get("content", ""),
                "due_date": card.get("due_date", baseline["due_date"]), "planned_for": card.get("planned_for", baseline["planned_for"])}
    previous_label = dict(Task.Status.choices).get(baseline["status"], baseline["status"])
    lines = [f'{previous_label} → {task.get_status_display()} · {baseline["progress"]}% → {task.progress}%']
    lines.extend(content_lines(card))
    lines.append(f'截止日期：{baseline["due_date"] or "未设置"} → {card["due_date"] or "未设置"}')
    lines.append(f'安排日期：{baseline["planned_for"] or "未设置"} → {card["planned_for"] or "未设置"}')
    if historical:
        lines.append("仅补记历史，不更新当前任务")
    summary = "\n".join(lines)
    return {"task": task, "card": card, "baseline": baseline, "snapshot": snapshot}, summary


def prepare_other(card, session):
    kind = card["kind"]
    project = Project.objects.get(pk=card["project_id"]) if card.get("project_id") else None
    person = Person.objects.get(pk=card["person_id"]) if card.get("person_id") else None
    if kind != "note" and not project:
        raise ManualMeetingError("新增任务、风险和待决策记录需要选择项目。")
    snapshot = {"title": card.get("title") or {"new_task": "新任务", "risk": "风险", "decision": "待决策", "note": "纪要"}[kind],
                "project_name": project.name if project else "", "person_name": person.name if person else ""}
    due = date.fromisoformat(card["due_date"]) if card.get("due_date") else None
    model = None
    if kind == "new_task":
        model = Task(project=project, assignee=person, phase_id=card.get("phase_id"), title=card.get("title", ""),
                     status=card.get("status") or Task.Status.NOT_STARTED, progress=card.get("progress") or 0,
                     due_date=due, planned_for=date.fromisoformat(card["planned_for"]) if card.get("planned_for") else None,
                     description=card.get("content", ""), source_meeting=session.meeting_note,
                     current_note="\n".join(filter(None, [card.get("completed_work"), card.get("next_step")])))
        if model.status == Task.Status.DONE:
            model.progress = 100
        model.full_clean()
        snapshot["phase_name"] = model.phase.name if model.phase_id else ""
        summary = "\n".join([f'{model.get_status_display()} · {model.progress}%', *content_lines(card),
            f'项目阶段：{snapshot["phase_name"] or "未设置"}',
            f'截止日期：{card["due_date"] or "未设置"}', f'安排日期：{card["planned_for"] or "未设置"}'])
    else:
        content = card.get("content", "").strip()
        if not content:
            raise ManualMeetingError("请填写风险、待决策或纪要内容。")
        if kind != "note":
            linked = Task.objects.get(pk=card["task_id"]) if card.get("task_id") else None
            if linked and linked.project_id != project.pk:
                raise ManualMeetingError("关联任务必须属于所选项目。")
            model = Risk(project=project, task=linked, owner=person, content=content, due_date=due,
                         risk_type=Risk.Type.DECISION if kind == "decision" else Risk.Type.RISK,
                         source_meeting=session.meeting_note)
            model.full_clean()
        summary = content + (f'；截止日期：{card["due_date"]}' if card.get("due_date") else "")
    return {"card": card, "model": model, "snapshot": snapshot}, summary, snapshot


def content_lines(card):
    return [f"{label}：{card[key]}" for key, label in (("completed_work", "本次完成"), ("next_step", "下一步"), ("content", "补充说明")) if card.get(key)]
