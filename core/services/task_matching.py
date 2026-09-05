import re
from dataclasses import dataclass
from difflib import SequenceMatcher


@dataclass(frozen=True)
class TaskCandidate:
    task_id: int
    title: str
    score: float


def _normalize_title(value: str) -> str:
    value = re.sub(r"[\s，。、“”：（）()\-_/]+", "", value or "").lower()
    for prefix in ("完成", "处理", "开发", "进行", "配合"):
        if value.startswith(prefix):
            value = value[len(prefix):]
    return value


def find_task_candidates(item: dict, queryset) -> list[TaskCandidate]:
    wanted = _normalize_title(item.get("title", ""))
    if not wanted:
        return []
    candidates = []
    tasks = queryset.select_related("project", "assignee") if hasattr(queryset, "select_related") else queryset
    for task in tasks:
        if item.get("project_name") and task.project.name != item["project_name"]:
            continue
        if item.get("assignee_name") and (not task.assignee or task.assignee.name != item["assignee_name"]):
            continue
        existing = _normalize_title(task.title)
        score = SequenceMatcher(None, wanted, existing).ratio()
        if wanted in existing or existing in wanted:
            score = max(score, 0.9)
        if score >= 0.66:
            candidates.append(TaskCandidate(task.id, task.title, round(score, 3)))
    return sorted(candidates, key=lambda value: value.score, reverse=True)[:3]
