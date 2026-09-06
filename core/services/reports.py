from dataclasses import dataclass
from datetime import date, datetime, time

from django.utils import timezone
from django.db.models import Q

from core.models import Milestone, ProgressUpdate, Risk, Task


@dataclass(frozen=True)
class WeeklyReport:
    markdown: str


def build_weekly_report(start: date, end: date) -> WeeklyReport:
    start_dt = timezone.make_aware(datetime.combine(start, time.min))
    end_dt = timezone.make_aware(datetime.combine(end, time.max))
    updates = ProgressUpdate.objects.filter(
        Q(occurred_on__range=(start, end)) |
        Q(occurred_on__isnull=True, recorded_at__range=(start_dt, end_dt))
    ).select_related("task__project", "task")
    status_labels = dict(Task.Status.choices)
    projects = {}
    for update in updates:
        snapshot = update.snapshot or {}
        title = snapshot.get("title") or update.task.title
        project_name = snapshot.get("project_name") or update.task.project.name
        row = projects.setdefault(project_name, {"done": [], "progress": [], "next": []})
        if update.completed_work:
            row["done"].append(f"{title}：{update.completed_work}")
        elif update.new_status == Task.Status.DONE:
            row["done"].append(f"{title}（已完成）")
        if update.new_status and update.new_status != Task.Status.DONE:
            row["progress"].append(f"{title}（{status_labels.get(update.new_status, update.new_status)}）")
        if update.next_step:
            row["next"].append(f"{title}：{update.next_step}")
    risks = Risk.objects.filter(
        Q(source_meeting__meeting_date__range=(start, end)) |
        Q(source_meeting__isnull=True, created_at__date__range=(start, end))
    ).select_related("project")
    milestones = Milestone.objects.filter(updated_at__date__range=(start, end)).select_related("project")
    lines = [f"# 项目周报（{start:%Y-%m-%d} ～ {end:%Y-%m-%d}）", ""]
    for project_name, sections in projects.items():
        lines.extend([f"## {project_name}", "", "### 本期完成"])
        lines.extend([f"- {v}" for v in sections["done"]] or ["- 无"])
        lines.extend(["", "### 进行中"])
        lines.extend([f"- {v}" for v in sections["progress"]] or ["- 无"])
        lines.extend(["", "### 下一步"])
        lines.extend([f"- {v}" for v in sections["next"]] or ["- 无"])
        lines.append("")
    lines.extend(["## 风险与待决策", ""])
    lines.extend([f"- 【{r.project.name}】{r.get_risk_type_display()}：{r.content}" for r in risks] or ["- 无"])
    lines.extend(["", "## 里程碑变化", ""])
    lines.extend([f"- 【{m.project.name}】{m.name}：{m.target_date or '日期待确认'}" for m in milestones] or ["- 无"])
    return WeeklyReport("\n".join(lines))
