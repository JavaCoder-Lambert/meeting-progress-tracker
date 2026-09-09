from datetime import timedelta

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError, OperationalError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from core.models import Project
from core.weekplan_models import ProjectWeekPlan
from core.services.weekplans import create_draft, save_draft, save_and_publish_plan, copy_plan, review_rows


class WeekForm(forms.Form):
    week = forms.DateField(label="业务周（任选该周一天）", widget=forms.DateInput(attrs={"type": "date"}))


@login_required
@require_http_methods(["GET", "POST"])
def weekplan_list(request, project_pk):
    project = get_object_or_404(Project, pk=project_pk)
    form = WeekForm(request.POST or None, initial={"week": timezone.localdate()})
    if request.method == "POST" and form.is_valid():
        try:
            plan = create_draft(project, form.cleaned_data["week"])
            return redirect("weekplan_detail", pk=plan.pk)
        except ValidationError as exc:
            form.add_error(None, exc)
        except (IntegrityError, OperationalError):
            form.add_error(None, "计划正在被更新，请刷新重试。")
    page = Paginator(project.weekplans.all(), 25).get_page(request.GET.get("page"))
    return render(request, "core/weekplan_list.html", {"project": project, "form": form, "page_obj": page})


@login_required
@require_http_methods(["GET", "POST"])
def weekplan_detail(request, pk):
    plan = get_object_or_404(ProjectWeekPlan.objects.select_related("project", "source"), pk=pk)
    error = ""
    rechecked = False
    posted_draft = request.method == "POST" and request.POST.get("action") in {"save", "publish", "recheck"}
    form_version = request.POST.get("version", "") if posted_draft else plan.version
    if request.method == "POST":
        try:
            action = request.POST.get("action")
            if action in {"save", "publish"}:
                ids = [int(value) for value in request.POST.getlist("tasks")]
                save = save_and_publish_plan if action == "publish" else save_draft
                plan = save(pk, int(request.POST["version"]), {tid: request.POST.get(f"delivery_{tid}", "") for tid in ids})
            elif action == "recheck":
                if plan.status != "draft":
                    raise ValidationError("计划已发布，请从发布版本发起修订。")
                if int(request.POST.get("reviewed_version", "-1")) != plan.version:
                    raise ValidationError("核对期间草稿再次变化，请重新核对当前已保存内容。")
                form_version = plan.version
                rechecked = True
            elif action in {"revise", "carry"}:
                plan = copy_plan(pk, request.POST.get("reason", ""), carry=action == "carry")
            else:
                raise ValidationError("未知操作。")
            if not rechecked:
                messages.success(request, "周计划已更新。")
                return redirect("weekplan_detail", pk=plan.pk)
        except ValidationError as exc:
            error = "；".join(exc.messages)
        except (ValueError, KeyError):
            error = "表单数据无效，请刷新后重试。"
        except (IntegrityError, OperationalError):
            error = "计划正在被更新，请刷新后重试。"
        plan.refresh_from_db()
    deliveries = {item.task_id: item.delivery for item in plan.items.all()}
    selected = set(request.POST.getlist("tasks")) if posted_draft else {str(tid) for tid in deliveries}
    choices = [{"task": task, "selected": str(task.pk) in selected,
        "delivery": request.POST.get(f"delivery_{task.pk}", "") if posted_draft else deliveries.get(task.pk, "")} for task in plan.project.tasks.select_related("assignee")]
    rows = review_rows(plan) if plan.status == "published" else []
    today = timezone.localdate()
    end = plan.week_start + timedelta(days=6)
    review_title = "业务周尚未开始" if plan.week_start > today else ("截至今天的进展（本周尚未结束）" if end >= today else "周末实际")
    return render(request, "core/weekplan_detail.html", {"plan": plan, "choices": choices, "rows": rows,
        "end": plan.week_start + timedelta(days=6), "now": timezone.localdate(), "error": error,
        "form_version": form_version, "rechecked": rechecked, "posted_draft": posted_draft,
        "current_items": plan.items.select_related("task"),
        "review_title": review_title, "cutoff": min(today, end), "future_week": plan.week_start > today,
        "done_count": sum(row["done"] for row in rows)}, status=409 if error else 200)
