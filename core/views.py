from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .forms import MeetingNoteForm, PersonForm, ProjectForm, TaskForm
from .models import ImportDraft, MeetingNote, Person, Project, Task
from .services.dashboard import dashboard_context
from .services.draft_confirmation import DraftConfirmationError, confirm_draft
from .services.exports import build_csv_zip, build_json_export
from .services.llm_client import LLMParseError, parse_meeting_note
from .services.reports import build_weekly_report
from .services.task_matching import find_task_candidates


def health(request):
    return JsonResponse({"status": "ok"})


@login_required
def dashboard(request):
    return render(request, "core/dashboard.html", dashboard_context())


@login_required
def meeting_create(request):
    form = MeetingNoteForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        note = form.save()
        return redirect("meeting_detail", pk=note.pk)
    return render(request, "core/meeting_form.html", {"form": form})


@login_required
def meeting_detail(request, pk):
    note = get_object_or_404(MeetingNote, pk=pk)
    return render(request, "core/meeting_detail.html", {"note": note, "latest_draft": note.drafts.first()})


@login_required
def meeting_parse(request, pk):
    note = get_object_or_404(MeetingNote, pk=pk)
    if request.method != "POST":
        return redirect("meeting_detail", pk=pk)
    try:
        draft = parse_meeting_note(note)
    except LLMParseError as exc:
        messages.error(request, str(exc))
        return redirect("meeting_detail", pk=pk)
    return redirect("draft_review", pk=draft.pk)


def _draft_context(draft, error=""):
    payload = draft.payload
    task_rows = []
    for item in payload.get("tasks", []):
        task_rows.append({"item": item, "candidates": find_task_candidates(item, Task.objects.exclude(status=Task.Status.DONE))})
    return {"draft": draft, "note": draft.meeting_note, "task_rows": task_rows, "projects": Project.objects.all(), "people": Person.objects.filter(is_active=True), "error": error}


@login_required
def draft_review(request, pk):
    draft = get_object_or_404(ImportDraft.objects.select_related("meeting_note"), pk=pk)
    return render(request, "core/draft_review.html", _draft_context(draft))


@login_required
def draft_confirm(request, pk):
    draft = get_object_or_404(ImportDraft.objects.select_related("meeting_note"), pk=pk)
    if request.method != "POST":
        return redirect("draft_review", pk=pk)
    payload = draft.payload.copy()
    decisions = {"tasks": [], "risks": [], "milestones": []}
    for index, item in enumerate(payload.get("tasks", [])):
        edited = item.copy()
        for field in ("title", "description", "status", "priority", "current_note", "completed_work", "next_step", "planned_start_date", "due_date", "acceptance_date"):
            if f"task_{index}_{field}" in request.POST:
                edited[field] = request.POST.get(f"task_{index}_{field}") or None if field.endswith("_date") else request.POST.get(f"task_{index}_{field}", "")
        if f"task_{index}_progress" in request.POST:
            try: edited["progress"] = int(request.POST[f"task_{index}_progress"])
            except ValueError: edited["progress"] = -1
        payload["tasks"][index] = edited
        decisions["tasks"].append({
            "action": request.POST.get(f"task_{index}_action", "ignore"),
            "project_id": request.POST.get(f"task_{index}_project") or None,
            "assignee_id": request.POST.get(f"task_{index}_assignee") or None,
            "task_id": request.POST.get(f"task_{index}_existing") or None,
        })
    for kind in ("risks", "milestones"):
        for index, _item in enumerate(payload.get(kind, [])):
            row = {"action": request.POST.get(f"{kind}_{index}_action", "ignore"), "project_id": request.POST.get(f"{kind}_{index}_project") or None}
            if kind == "risks": row["owner_id"] = request.POST.get(f"risks_{index}_owner") or None
            decisions[kind].append(row)
    draft.payload = payload
    draft.save(update_fields=["payload"])
    try:
        result = confirm_draft(draft.pk, decisions)
    except (DraftConfirmationError, ValueError) as exc:
        return render(request, "core/draft_review.html", _draft_context(draft, f"请修正：{exc}"), status=200)
    messages.success(request, f"已入库：新建 {result.created_tasks} 个任务，更新 {result.updated_tasks} 个任务。")
    return redirect("meeting_detail", pk=draft.meeting_note_id)


@login_required
def task_list(request):
    tasks = Task.objects.select_related("project", "assignee")
    for field in ("project", "assignee", "status", "priority"):
        value = request.GET.get(field)
        if value: tasks = tasks.filter(**{field: value})
    return render(request, "core/task_list.html", {"tasks": tasks, "projects": Project.objects.all(), "people": Person.objects.filter(is_active=True), "statuses": Task.Status.choices})


@login_required
def task_board(request):
    tasks = Task.objects.select_related("project", "assignee")
    columns = [(value, label, tasks.filter(status=value)) for value, label in Task.Status.choices]
    return render(request, "core/task_board.html", {"columns": columns})


@login_required
def task_edit(request, pk=None):
    task = get_object_or_404(Task, pk=pk) if pk else None
    old_progress, old_status = (task.progress, task.status) if task else (None, "")
    form = TaskForm(request.POST or None, instance=task)
    if request.method == "POST" and form.is_valid():
        task = form.save()
        if old_progress != task.progress or old_status != task.status:
            task.progress_updates.create(previous_progress=old_progress, new_progress=task.progress, previous_status=old_status, new_status=task.status)
        return redirect("task_list")
    return render(request, "core/form_page.html", {"form": form, "title": "编辑任务" if task else "新建任务"})


def _model_form_view(request, form_class, instance, title, redirect_name):
    form = form_class(request.POST or None, instance=instance)
    if request.method == "POST" and form.is_valid():
        form.save(); return redirect(redirect_name)
    return render(request, "core/form_page.html", {"form": form, "title": title})


@login_required
def project_list(request):
    return render(request, "core/project_list.html", {"projects": Project.objects.all()})


@login_required
def project_detail(request, pk):
    project = get_object_or_404(Project, pk=pk)
    return render(request, "core/project_detail.html", {"project": project})


@login_required
def project_edit(request, pk=None):
    return _model_form_view(request, ProjectForm, get_object_or_404(Project, pk=pk) if pk else None, "编辑项目" if pk else "新建项目", "project_list")


@login_required
def person_list(request):
    return render(request, "core/person_list.html", {"people": Person.objects.all()})


@login_required
def person_detail(request, pk):
    person = get_object_or_404(Person, pk=pk)
    return render(request, "core/person_detail.html", {"person": person})


@login_required
def person_edit(request, pk=None):
    return _model_form_view(request, PersonForm, get_object_or_404(Person, pk=pk) if pk else None, "编辑人员" if pk else "新增人员", "person_list")


@login_required
def report_view(request):
    today = timezone.localdate()
    start = request.GET.get("start") or (today - timezone.timedelta(days=today.weekday())).isoformat()
    end = request.GET.get("end") or today.isoformat()
    report = build_weekly_report(timezone.datetime.fromisoformat(start).date(), timezone.datetime.fromisoformat(end).date())
    return render(request, "core/report.html", {"start": start, "end": end, "markdown": report.markdown})


@login_required
def settings_view(request):
    from django.conf import settings
    return render(request, "core/settings.html", {"llm_ready": bool(settings.LLM_API_KEY and settings.LLM_MODEL)})


@login_required
def export_json(request):
    response = HttpResponse(build_json_export(), content_type="application/json; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="tracker-export.json"'
    return response


@login_required
def export_csv(request):
    response = HttpResponse(build_csv_zip(), content_type="application/zip")
    response["Content-Disposition"] = 'attachment; filename="tracker-csv.zip"'
    return response
