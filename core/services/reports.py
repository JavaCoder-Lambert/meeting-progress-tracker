from dataclasses import dataclass
from datetime import date, datetime, time

from django.utils import timezone

from core.models import Milestone, ProgressUpdate, Risk, Task


@dataclass(frozen=True)
class WeeklyReport:
    markdown: str


def build_weekly_report(start: date, end: date) -> WeeklyReport:
    start_dt = timezone.make_aware(datetime.combine(start, time.min))
    end_dt = timezone.make_aware(datetime.combine(end, time.max))
    updates = ProgressUpdate.objects.filter(recorded_at__range=(start_dt, end_dt)).select_related("task__project", "task")
    projects = {}
    for update in updates:
        row = projects.setdefault(update.task.project.name, {"done": [], "progress": [], "next": []})
        if update.completed_work:
            row["done"].append(f"{update.task.title}：{update.completed_work}")
        if update.new_status and update.new_status != Task.Status.DONE:
            row["progress"].append(f"{update.task.title}（{update.task.get_status_display()}）")
        if update.next_step:
            row["next"].append(f"{update.task.title}：{update.next_step}")
    risks = Risk.objects.filter(created_at__date__range=(start, end)).select_related("project")
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
