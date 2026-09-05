from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from .forms import MilestoneForm, ProjectPhaseForm, RiskForm, TaskScheduleForm
from .models import Milestone, Person, Project, ProjectPhase, Risk, Task
from .services.planning import plan_period, planned_tasks, safe_date, timeline_context


def _filter_id(queryset, field, value):
    if value and value.isdecimal() and len(value) < 12:
        return queryset.filter(**{field: value})
    return queryset


@login_required
def plan_board(request):
    view = request.GET.get("view", "week")
    if view not in {"today", "week", "next", "unscheduled"}:
        view = "week"
    today = timezone.localdate()
    anchor = safe_date(request.GET.get("date"))
    start, end = plan_period(view, anchor)
    queryset = Task.objects.select_related("project", "assignee", "phase").exclude(project__status=Project.Status.ARCHIVED)
    project_id, assignee_id = request.GET.get("project", ""), request.GET.get("assignee", "")
    queryset = _filter_id(queryset, "project_id", project_id)
    queryset = _filter_id(queryset, "assignee_id", assignee_id)
    show_done = request.GET.get("show_done") == "1"
    tasks = planned_tasks(queryset, view, start, end, show_done)
    page = Paginator(tasks, 25).get_page(request.GET.get("page"))
    for task in page:
        task.carried_over = bool(task.planned_for and task.planned_for < min(start, today) and task.status != Task.Status.DONE)
    filters = request.GET.copy()
    filters.pop("page", None)
    counts = queryset.aggregate(
        unscheduled=Count("pk", filter=Q(planned_for__isnull=True) & ~Q(status=Task.Status.DONE)),
        overdue=Count("pk", filter=Q(due_date__lt=today) & ~Q(status=Task.Status.DONE)),
    )
    return render(request, "core/plan_board.html", {
        "tasks": page, "page_obj": page, "querystring": filters.urlencode(),
        "view": view, "start": start, "end": end, "today": today,
        "previous_week": start - timedelta(days=7), "next_week": start + timedelta(days=7),
        "projects": Project.objects.exclude(status=Project.Status.ARCHIVED), "people": Person.objects.filter(is_active=True),
        "project_filter": project_id, "assignee_filter": assignee_id, "show_done": show_done, "counts": counts,
    })


@login_required
@require_POST
def task_schedule(request, pk):
    task = get_object_or_404(Task, pk=pk)
    form = TaskScheduleForm(request.POST)
    destination = request.POST.get("next", "")
    if not destination.startswith("/") or not url_has_allowed_host_and_scheme(destination, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        destination = reverse("plan_board")
    if form.is_valid():
        today = timezone.localdate()
        action = form.cleaned_data["action"]
        dates = {"today": today, "next_week": today - timedelta(days=today.weekday()) + timedelta(days=7),
                 "clear": None, "date": form.cleaned_data["planned_for"]}
        # Scheduling is not a progress update: do not reset the stale-work clock.
        Task.objects.filter(pk=task.pk).update(planned_for=dates[action])
        messages.success(request, "已取消安排。" if action == "clear" else "已更新安排，截止日期保持不变。")
    else:
        messages.error(request, "安排未保存，请选择有效的日期和操作。")
    return redirect(destination)


@login_required
def project_plan(request, pk):
    project = get_object_or_404(Project, pk=pk)
    stats = project.tasks.aggregate(
        total=Count("pk"), done=Count("pk", filter=Q(status=Task.Status.DONE)),
        overdue=Count("pk", filter=Q(due_date__lt=timezone.localdate()) & ~Q(status=Task.Status.DONE)),
    )
    context = timeline_context(project, safe_date(request.GET.get("start")))
    context.update({"project": project, "stats": stats,
                    "task_completion": round(stats["done"] / stats["total"] * 100) if stats["total"] else 0,
                    "project_tasks": project.tasks.select_related("assignee", "phase")[:50],
                    "project_risks": project.risks.select_related("owner").exclude(status__in=[Risk.Status.CLOSED, Risk.Status.RESOLVED]),
                    "closed_risks": project.risks.select_related("owner").filter(status__in=[Risk.Status.CLOSED, Risk.Status.RESOLVED]),
                    "milestones": project.milestones.all(),
                    "timeline_tab": request.GET.get("tab") == "timeline", "today": timezone.localdate()})
    return render(request, "core/project_detail.html", context)


@login_required
def phase_edit(request, project_pk=None, pk=None):
    phase = get_object_or_404(ProjectPhase, pk=pk) if pk else None
    project = phase.project if phase else get_object_or_404(Project, pk=project_pk)
    form = ProjectPhaseForm(request.POST or None, instance=phase or ProjectPhase(project=project))
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "项目阶段已保存。")
        return redirect("project_detail", pk=project.pk)
    return render(request, "core/plan_form.html", {"form": form, "project": project, "title": "编辑阶段" if phase else "新建阶段"})


@login_required
def milestone_edit(request, project_pk=None, pk=None):
    milestone = get_object_or_404(Milestone, pk=pk) if pk else None
    project = milestone.project if milestone else get_object_or_404(Project, pk=project_pk)
    form = MilestoneForm(request.POST or None, instance=milestone or Milestone(project=project))
    form.fields["project"].queryset = Project.objects.filter(pk=project.pk)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "里程碑已保存。")
        return redirect("project_detail", pk=project.pk)
    return render(request, "core/plan_form.html", {"form": form, "project": project, "title": "编辑里程碑" if milestone else "新建里程碑"})


@login_required
def risk_edit(request, project_pk=None, pk=None):
    risk = get_object_or_404(Risk, pk=pk) if pk else None
    project = risk.project if risk else get_object_or_404(Project, pk=project_pk)
    form = RiskForm(request.POST or None, instance=risk or Risk(project=project), project=project)
    if request.method == "POST" and form.is_valid():
        risk = form.save(commit=False)
        if risk.status in {Risk.Status.CLOSED, Risk.Status.RESOLVED}:
            risk.resolved_at = risk.resolved_at or timezone.now()
        else:
            risk.resolved_at = None
        risk.save()
        messages.success(request, "风险记录已保存。")
        return redirect("project_detail", pk=project.pk)
    return render(request, "core/plan_form.html", {"form": form, "project": project, "title": "更新风险" if risk else "记录风险"})
