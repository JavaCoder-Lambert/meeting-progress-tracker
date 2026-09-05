from datetime import date, timedelta

from django.db.models import Q
from django.utils import timezone

from core.models import Task


def safe_date(value, default=None):
    default = default or timezone.localdate()
    try:
        result = date.fromisoformat(value or "")
        return result if 1900 <= result.year <= 2200 else default
    except (TypeError, ValueError):
        return default


def plan_period(view, anchor=None):
    today = timezone.localdate()
    anchor = anchor or today
    monday = anchor - timedelta(days=anchor.weekday())
    if view == "today":
        return today, today
    if view == "next":
        monday = today - timedelta(days=today.weekday()) + timedelta(days=7)
    return monday, monday + timedelta(days=6)


def planned_tasks(queryset, view, start, end, show_done=False):
    if not show_done:
        queryset = queryset.exclude(status=Task.Status.DONE)
    if view == "unscheduled":
        return queryset.filter(planned_for__isnull=True).order_by("due_date", "project__name", "pk")
    # Missed arrangements stay actionable when the calendar rolls forward.
    cutoff = min(start, timezone.localdate())
    return queryset.filter(
        Q(planned_for__range=(start, end))
        | (Q(planned_for__lt=cutoff) & ~Q(status=Task.Status.DONE))
    ).order_by("planned_for", "project__name", "pk")


def timeline_context(project, anchor):
    start = anchor - timedelta(days=anchor.weekday())
    weeks = [start + timedelta(days=i * 7) for i in range(8)]
    end = start + timedelta(days=55)
    rows = []

    def add(label, kind, item, begin, finish, url_name):
        visible_start = max(begin or finish or start, start)
        visible_end = min(finish or begin or end, end)
        dated = bool(begin or finish)
        visible = dated and visible_start <= visible_end
        rows.append({
            "label": label, "kind": kind, "item": item, "start": begin, "end": finish,
            "url_name": url_name, "dated": dated, "visible": visible,
            "offset": round((visible_start - start).days / 56 * 100, 3) if visible else 0,
            "width": round(((visible_end - visible_start).days + 1) / 56 * 100, 3) if visible else 0,
        })

    phases = list(project.phases.all())
    for phase in phases:
        add(phase.name, "phase", phase, phase.start_date, phase.end_date, "phase_edit")
    for milestone in project.milestones.all():
        add(milestone.name, "milestone", milestone, milestone.target_date, milestone.target_date, "milestone_edit")
    for task in project.tasks.select_related("assignee", "phase").all()[:100]:
        add(task.title, "task", task, task.planned_start_date, task.due_date, "task_detail")
    return {"timeline_rows": rows, "timeline_start": start, "timeline_end": end, "weeks": weeks,
            "previous_window": start - timedelta(days=56), "next_window": start + timedelta(days=56),
            "phases": phases}
