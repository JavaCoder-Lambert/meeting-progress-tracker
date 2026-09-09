"""Bounded, JSON-only normalization; incomplete card content is valid in a draft."""
from datetime import date, time
from uuid import UUID

from django.core import signing
from core.models import Person, Project, ProjectPhase, Task

from .manual_meetings import ManualMeetingError


def normalize_state(value, check_refs=True):
    if not isinstance(value, dict):
        raise ManualMeetingError("会议草稿必须是对象。")
    state = {"title": text(value.get("title", ""), 200), "meeting_date": date_value(value.get("meeting_date", "")),
             "meeting_time": text(value.get("meeting_time", ""), 5), "agenda": text(value.get("agenda", "")),
             "project_ids": id_list(value.get("project_ids", [])), "person_ids": id_list(value.get("person_ids", [])), "items": []}
    if not state["title"] or not state["meeting_date"]:
        raise ManualMeetingError("请填写会议主题和日期。")
    if state["meeting_time"]:
        try:
            if time.fromisoformat(state["meeting_time"]).strftime("%H:%M") != state["meeting_time"]:
                raise ValueError
        except ValueError as exc:
            raise ManualMeetingError("会议时间应为 HH:MM。") from exc
    cards = value.get("items", [])
    if not isinstance(cards, list) or len(cards) > 300:
        raise ManualMeetingError("会议记录必须为列表，最多 300 项。")
    ids, linked = set(), set()
    for raw in cards:
        if not isinstance(raw, dict):
            raise ManualMeetingError("记录格式无效。")
        try:
            uid = str(UUID(raw.get("id", "")))
        except (ValueError, TypeError, AttributeError) as exc:
            raise ManualMeetingError("记录需要有效的 UUID。") from exc
        if uid in ids:
            raise ManualMeetingError("记录 ID 重复。")
        ids.add(uid)
        kind = text(raw.get("kind", ""), 16)
        if kind not in {"task", "new_task", "risk", "decision", "note"}:
            raise ManualMeetingError("记录类型无效。")
        card = {"id": uid, "kind": kind}
        for key in ("task_id", "project_id", "person_id", "phase_id"):
            card[key] = identifier(raw.get(key))
        if kind == "task" and card["task_id"]:
            if card["task_id"] in linked:
                raise ManualMeetingError("同一任务只能保留一张记录卡。")
            linked.add(card["task_id"])
        for key in ("title", "status", "completed_work", "next_step", "content", "baseline"):
            card[key] = text(raw.get(key, ""), 240 if key == "title" else 24 if key == "status" else 20000)
        card["recorded"] = raw.get("recorded", False)
        if not isinstance(card["recorded"], bool):
            raise ManualMeetingError("已记录标记必须是布尔值。")
        progress = raw.get("progress", "")
        if progress != "" and (type(progress) is not int or not 0 <= progress <= 100):
            raise ManualMeetingError("进度应为 0 到 100 的整数。")
        card["progress"] = progress
        baseline = signed_baseline(card)
        card["baseline_values"] = baseline_values(card)
        for key in ("due_date", "planned_for"):
            card[key] = date_value(raw.get(key, baseline.get(key, "") if isinstance(baseline, dict) else ""))
        state["items"].append(card)
    if check_refs:
        check_references(state)
    followups = value.get("risk_followups", [])
    if not isinstance(followups, list) or len(followups) > 300:
        raise ManualMeetingError("风险跟进必须为列表，最多 300 项。")
    if followups:
        state["risk_followups"] = []
        seen = set()
        for raw in followups:
            if not isinstance(raw, dict):
                raise ManualMeetingError("风险跟进格式无效。")
            entry = {key: text(raw.get(key, ""), 100 if key == "token" else 20000)
                     for key in ("response", "next_step", "reason", "status", "baseline", "token")}
            entry.update(risk_id=identifier(raw.get("risk_id")), owner_id=identifier(raw.get("owner_id")), due_date=date_value(raw.get("due_date", "")))
            if not entry["risk_id"] or entry["risk_id"] in seen:
                raise ManualMeetingError("同一风险只能保留一条本次跟进。")
            seen.add(entry["risk_id"])
            state["risk_followups"].append(entry)
    return state


def signed_baseline(card):
    if card.get("kind") != "task" or not isinstance(card.get("baseline"), str):
        return {}
    try:
        baseline = signing.loads(card["baseline"], salt="manual-meeting-task")
    except signing.BadSignature:
        return {}  # Retain broken baselines in drafts; confirmation explains them.
    return baseline if isinstance(baseline, dict) and baseline.get("id") == card.get("task_id") else {}


def baseline_values(card):
    baseline = signed_baseline(card)
    fields = ("status", "progress", "title", "project_name", "assignee_name", "due_date", "planned_for")
    return {key: baseline[key] for key in fields} if all(key in baseline for key in fields) else {}


def text(value, limit=20000):
    if not isinstance(value, str) or len(value) > limit:
        raise ManualMeetingError(f"文本格式无效或超过 {limit} 字。")
    return value.strip()


def date_value(value):
    value = text(value, 10)
    if value:
        try:
            if date.fromisoformat(value).isoformat() != value:
                raise ValueError
        except ValueError as exc:
            raise ManualMeetingError("日期应为 YYYY-MM-DD。") from exc
    return value


def identifier(value):
    if value is None:
        return None
    if type(value) is not int or not 0 < value <= 9223372036854775807:
        raise ManualMeetingError("关联 ID 应为正整数。")
    return value


def id_list(value):
    if not isinstance(value, list) or len(value) > 300:
        raise ManualMeetingError("项目和人员列表最多 300 项。")
    result = [identifier(entry) for entry in value]
    if None in result:
        raise ManualMeetingError("关联列表不能含空值。")
    return list(dict.fromkeys(result))


def check_references(state):
    for model, key, metadata, label in ((Project, "project_id", "project_ids", "项目"),
                                      (Person, "person_id", "person_ids", "人员"),
                                      (Task, "task_id", None, "任务")):
        ids = set(state.get(metadata, [])) if metadata else set()
        ids.update(card[key] for card in state["items"] if card[key])
        if ids - set(model.objects.filter(pk__in=ids).values_list("pk", flat=True)):
            raise ManualMeetingError(f"关联{label}不存在，请重新选择。")
    phase_ids = {card["phase_id"] for card in state["items"] if card.get("phase_id")}
    phases = dict(ProjectPhase.objects.filter(pk__in=phase_ids).values_list("pk", "project_id"))
    for card in state["items"]:
        if not card.get("phase_id"):
            continue
        if card["phase_id"] not in phases:
            raise ManualMeetingError("关联阶段不存在，请重新选择。")
        if card["kind"] != "new_task" or phases[card["phase_id"]] != card["project_id"]:
            raise ManualMeetingError("阶段必须属于新增任务所选项目。")
