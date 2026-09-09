"""Persist unfinished review edits separately from the original AI result."""
from copy import copy, deepcopy
from hashlib import sha256
import json

from django.core import signing
from django.utils import timezone

from core.models import ImportDraft, Task
from .ai_history import task_baseline
from .draft_review import build_draft_review
from .task_payload import TASK_DEFAULTS, task_provided_fields


class ReviewConflict(ValueError):
    pass


def check_review_version(draft, version):
    if str(version) != str(draft.review_version):
        raise ReviewConflict("草稿已在其他页面修改，请先复制当前未保存内容，再重新打开最新草稿；本次未覆盖。")


def check_editable(draft):
    siblings = ImportDraft.objects.filter(meeting_note_id=draft.meeting_note_id)
    if draft.confirmed_at or siblings.filter(confirmed_at__isnull=False).exists():
        raise ReviewConflict("该会议已经入库，不能再修改暂存内容。")
    if siblings.order_by("-created_at", "-pk").values_list("pk", flat=True).first() != draft.pk:
        raise ReviewConflict("已有新的解析草稿，本次未覆盖；请先复制当前内容，再打开最新草稿。")
    if draft.meeting_note.parse_status == "parsing":
        raise ReviewConflict("会议正在重新解析，请先保留当前内容，解析完成后再查看最新草稿。")


def review_snapshot(draft):
    snapshot = copy(draft)
    decisions = None
    if draft.review_state and not draft.confirmed_at:
        snapshot.payload = deepcopy(draft.review_state["payload"])
        decisions = deepcopy(draft.review_state["decisions"])
    return snapshot, decisions


def read_review_submission(draft, post):
    snapshot, saved_decisions = review_snapshot(draft)
    payload = deepcopy(snapshot.payload)
    rows = build_draft_review(snapshot, decisions=saved_decisions, preserve_values=bool(saved_decisions))["task_rows"]
    signed_rows = None
    if post.get("review_baseline"):
        try:
            baseline = signing.loads(post["review_baseline"], salt="draft-review")
            if baseline["draft_id"] != draft.pk or len(baseline["tasks"]) != len(rows):
                raise signing.BadSignature("草稿不匹配")
            signed_rows = baseline["tasks"]
        except (signing.BadSignature, KeyError, TypeError) as exc:
            raise signing.BadSignature("审阅表单已失效，请重新打开草稿。") from exc
    decisions = {"tasks": [], "risks": [], "milestones": []}
    for index, item in enumerate(payload.get("tasks", [])):
        provided = task_provided_fields(item)
        baseline = rows[index]["item"]
        if signed_rows is not None:
            baseline = {**baseline, **signed_rows[index]["values"]}
            # The signed page, not an earlier autosave, defines intentional edits.
            provided = set(signed_rows[index]["provided"])
        for field in ("title", "description", "status", "priority", "current_note", "completed_work", "next_step", "planned_start_date", "due_date", "acceptance_date", "progress"):
            key = f"task_{index}_{field}"
            if key not in post:
                continue
            value = post.get(key, "")
            if field.endswith("_date"):
                value = value or None
            elif field == "progress":
                try:
                    value = int(value)
                except ValueError:
                    pass  # A blank/incomplete field is valid for a stash, not import.
            if value != baseline.get(field):
                provided.add(field)
            item[field] = value
        item["_provided_fields"] = sorted(provided)
        planning_baseline = (signed_rows[index] if signed_rows is not None else rows[index])
        planning_provided = set(planning_baseline.get("planning_provided", []))
        planning = dict(rows[index]["planning"])
        for field, suffix in (("phase_id", "phase"), ("planned_for", "planned_for")):
            if f"task_{index}_{suffix}" in post:
                value = post.get(f"task_{index}_{suffix}", "")
                edited = post.get(f"task_{index}_{suffix}_edited", "")
                # JS records explicit edits separately from target autofill. An
                # empty marker preserves the native/no-JS value-difference path.
                if edited == "true" or (edited != "false" and value != planning_baseline.get("planning", {}).get(field, "")):
                    planning_provided.add(field)
                planning[field] = value
        decisions["tasks"].append({
            "action": post.get(f"task_{index}_action"),
            "project_id": post.get(f"task_{index}_project") or None,
            "assignee_id": post.get(f"task_{index}_assignee") or None,
            "task_id": post.get(f"task_{index}_existing") or None,
            "planning": planning,
            "planning_provided": sorted(planning_provided),
            "task_baseline": (signed_rows[index].get("task_baselines", {}).get(
                str(post.get(f"task_{index}_existing") or ""), "") if signed_rows is not None else ""),
        })
    for kind in ("risks", "milestones"):
        for index, item in enumerate(payload.get(kind, [])):
            field = "due_date" if kind == "risks" else "target_date"
            if f"{kind}_{index}_{field}" in post:
                item[field] = post.get(f"{kind}_{index}_{field}") or None
            row = {"action": post.get(f"{kind}_{index}_action"), "project_id": post.get(f"{kind}_{index}_project") or None}
            if kind == "risks":
                row["owner_id"] = post.get(f"risks_{index}_owner") or None
            decisions[kind].append(row)
    return payload, decisions


def refresh_task_baselines(draft, payload, decisions):
    for item, row in zip(payload.get("tasks", []), decisions.get("tasks", []), strict=True):
        if row.get("action") != "update" or not row.get("task_id"):
            continue
        try:
            task = Task.objects.select_related("project", "assignee").get(pk=row["task_id"])
        except (Task.DoesNotExist, ValueError) as exc:
            raise ValueError("关联任务不存在，请重新选择。") from exc
        row["task_baseline"] = task_baseline(task, draft)
        for field in TASK_DEFAULTS:
            if field not in task_provided_fields(item):
                item[field] = getattr(task, field)


def review_save_digest(post):
    # Preserve the submitted signed baseline and all form choices. Re-reading a
    # retry against saved payload could reinterpret which values were edited.
    fields = sorted((key, post.getlist(key)) for key in post if key not in {"csrfmiddlewaretoken", "review_version"})
    return sha256(json.dumps(fields, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def is_saved_review_retry(draft, version, digest):
    receipt = (draft.review_state or {}).get("save_receipt", {})
    return (receipt.get("version") == draft.review_version - 1
            and str(version) == str(receipt.get("version"))
            and receipt.get("digest") == digest)


def persist_review(draft, payload, decisions, *, save_digest=None):
    # Caller holds the transaction/lock, so version and content move together.
    draft.review_state = {"payload": payload, "decisions": decisions}
    if save_digest is not None:
        draft.review_state["save_receipt"] = {"version": draft.review_version, "digest": save_digest}
    draft.review_saved_at = timezone.now()
    draft.review_version += 1
    draft.save(update_fields=["review_state", "review_saved_at", "review_version"])


def reference_target(draft, destination):
    """Only known draft fields can become a signed create-and-return target."""
    kind, _, field = destination.partition(":")
    if kind not in {"project", "person"}:
        raise ValueError("新建类型无效。")
    if not field:
        return {"kind": kind, "group": "", "index": 0, "key": "", "name": ""}
    for group, prefix in (("tasks", "task"), ("risks", "risks"), ("milestones", "milestones")):
        key = "project_id" if kind == "project" else "assignee_id" if group == "tasks" else "owner_id"
        name_key = "project_name" if kind == "project" else "assignee_name" if group == "tasks" else "owner_name"
        suffix = key.removesuffix("_id")
        for index, item in enumerate(draft.payload.get(group, [])):
            if field == f"{prefix}_{index}_{suffix}" and (kind == "project" or group != "milestones"):
                return {"kind": kind, "group": group, "index": index, "key": key, "name": item.get(name_key, "")}
    raise ValueError("草稿字段无效。")
