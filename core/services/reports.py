from dataclasses import dataclass
from datetime import date, datetime, time

from django.utils import timezone
from django.db.models import Q
from django.db.models.functions import Coalesce, TruncDate

from core.models import Milestone, ProgressUpdate, Risk, RiskFollowup, Task
from .meeting_corrections import effective_progress_updates


@dataclass(frozen=True)
class WeeklyReport:
    markdown: str
    projects: list[dict]
    risks: list[str]
    milestones: list[str]
    followups: list[str]


def build_weekly_report(start: date, end: date) -> WeeklyReport:
    start_dt = timezone.make_aware(datetime.combine(start, time.min))
    end_dt = timezone.make_aware(datetime.combine(end, time.max))
    updates = ProgressUpdate.objects.filter(
        Q(occurred_on__range=(start, end)) |
        Q(occurred_on__isnull=True, recorded_at__range=(start_dt, end_dt))
    ).select_related("task__project", "task").annotate(
        business_date=Coalesce("occurred_on", TruncDate("recorded_at"))
    ).order_by("business_date", "recorded_at", "pk")
    status_labels = dict(Task.Status.choices)
    task_updates = {}
    for update in effective_progress_updates(updates):
        task_updates.setdefault(update.task_id, []).append(update)
    projects = {}
    for entries in sorted(task_updates.values(), key=lambda entries: (
        entries[-1].business_date, entries[-1].recorded_at, entries[-1].pk,
    )):
        update = entries[-1]
        snapshot = update.snapshot or {}
        title = snapshot.get("title") or update.task.title
        project_name = snapshot.get("project_name") or update.task.project.name
        project_key = snapshot.get("project_id") or update.task.project_id
        row = projects.setdefault(project_key, {
            "name": project_name, "tasks": [], "done": [], "progress": [], "next": [],
        })
        row["name"] = project_name
        status = update.new_status or snapshot.get("status", "")
        completed_work = [entry.completed_work for entry in entries if entry.completed_work]
        details = []
        for entry in entries:
            entry_snapshot = entry.snapshot or {}
            entry_status = entry.new_status or entry_snapshot.get("status", "")
            details.append({
                "date": entry.business_date,
                "status_label": status_labels.get(entry_status, "未记录"),
                "completed_work": entry.completed_work,
                "next_step": entry.next_step,
                "correction": getattr(entry, "applied_correction", None),
                "meeting_id": entry.meeting_note_id,
            })
        row["tasks"].append({
            "task_id": update.task_id,
            "title": title,
            "status": status,
            "status_label": status_labels.get(status, "未记录"),
            "progress": update.new_progress if update.new_progress is not None else snapshot.get("progress"),
            "date": update.business_date,
            "completed_work": completed_work,
            "next_step": update.next_step,
            "updates": details,
        })
        if completed_work:
            completion_label = "（已完成）" if status == Task.Status.DONE else ""
            row["done"].append(f"{title}：{'；'.join(completed_work)}{completion_label}")
        elif status == Task.Status.DONE:
            row["done"].append(f"{title}（已完成）")
        if status and status != Task.Status.DONE:
            row["progress"].append(f"{title}（{status_labels.get(status, status)}）")
        if update.next_step:
            row["next"].append(f"{title}：{update.next_step}")
    risks = Risk.objects.filter(
        Q(source_meeting__meeting_date__range=(start, end)) |
        Q(source_meeting__isnull=True, created_at__date__range=(start, end))
    ).select_related("project")
    milestones = Milestone.objects.filter(updated_at__date__range=(start, end)).select_related("project")
    risk_lines = [f"【{r.project.name}】{r.get_risk_type_display()}：{r.content}" for r in risks]
    followup_lines = []
    for followup in RiskFollowup.objects.filter(occurred_on__range=(start, end)).select_related("risk__project").order_by("occurred_on", "pk"):
        after = followup.after
        line = (f"【{after.get('project_name') or followup.risk.project.name}】"
                f"{after.get('content') or followup.risk.content}：{followup.response}"
                f"（{after.get('status_label', '状态未记录')}，{followup.occurred_on:%m-%d}）")
        if followup.next_step:
            line += f"；下一步：{followup.next_step}"
        followup_lines.append(line)
    milestone_lines = [f"【{m.project.name}】{m.name}：{m.target_date or '日期待确认'}" for m in milestones]
    lines = [f"# 项目周报（{start:%Y-%m-%d} ～ {end:%Y-%m-%d}）", ""]
    for sections in projects.values():
        lines.extend([f"## {sections['name']}", "", "### 本期进展"])
        lines.extend([f"- {v}" for v in sections["done"]] or ["- 无"])
        lines.extend(["", "### 进行中"])
        lines.extend([f"- {v}" for v in sections["progress"]] or ["- 无"])
        lines.extend(["", "### 下一步"])
        lines.extend([f"- {v}" for v in sections["next"]] or ["- 无"])
        lines.append("")
    lines.extend(["## 风险与待决策", "", "### 本周新增", ""])
    lines.extend([f"- {value}" for value in risk_lines] or ["- 无"])
    lines.extend(["", "### 本周跟进", ""])
    lines.extend([f"- {value}" for value in followup_lines] or ["- 无"])
    lines.extend(["", "## 里程碑变化", ""])
    lines.extend([f"- {value}" for value in milestone_lines] or ["- 无"])
    return WeeklyReport("\n".join(lines), list(projects.values()), risk_lines, milestone_lines, followup_lines)
