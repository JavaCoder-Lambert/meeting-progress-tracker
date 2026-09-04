from datetime import timedelta

from django.db.models import Count, Q
from django.utils import timezone

from core.models import MeetingNote, Project, Risk, Task


def dashboard_context():
    today = timezone.localdate()
    week_end = today + timedelta(days=7)
    tasks = Task.objects.select_related("project", "assignee")
    return {
        "metrics": {
            "in_progress": tasks.filter(status=Task.Status.IN_PROGRESS).count(),
            "acceptance": tasks.filter(status=Task.Status.ACCEPTANCE).count(),
            "delayed": tasks.filter(Q(status=Task.Status.DELAYED) | Q(due_date__lt=today)).exclude(status=Task.Status.DONE).distinct().count(),
            "blocked": tasks.filter(status=Task.Status.BLOCKED).count(),
        },
        "projects": Project.objects.annotate(task_count=Count("tasks"))[:10],
        "due_tasks": tasks.filter(due_date__lte=week_end).exclude(status=Task.Status.DONE)[:10],
        "risks": Risk.objects.exclude(status__in=[Risk.Status.RESOLVED, Risk.Status.CLOSED]).select_related("project")[:8],
        "meetings": MeetingNote.objects.all()[:6],
    }
