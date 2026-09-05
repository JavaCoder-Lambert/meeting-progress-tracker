from copy import deepcopy
from datetime import date, timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.db import transaction
from django.db.models import Prefetch
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from .forms import MeetingNoteForm, PersonForm, ProjectForm, TaskForm, TaskProgressForm
from .models import ImportDraft, MeetingNote, Person, Project, Task
from .services.dashboard import dashboard_context
from .services.draft_confirmation import DraftConfirmationError, confirm_draft
from .services.exports import build_csv_zip, build_json_export
from .services.llm_client import LLMParseError, parse_meeting_note
from .services.reports import build_weekly_report
from .services.draft_review import build_draft_review
from .services.progress_updates import record_task_progress, sync_task_completion_timestamp
from .services.task_payload import TASK_DEFAULTS, task_provided_fields


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
        if form.cleaned_data.get("intent") == "parse":
            request.session["auto_parse_note_id"] = note.pk
            return redirect(f"{reverse('meeting_detail', args=[note.pk])}?auto_parse=1")
        return redirect("meeting_detail", pk=note.pk)
    return render(request, "core/meeting_form.html", {"form": form})


@login_required
def meeting_detail(request, pk):
    note = get_object_or_404(
        MeetingNote.objects.prefetch_related(
            Prefetch("drafts", queryset=ImportDraft.objects.order_by("-created_at", "-pk"), to_attr="ordered_drafts")
        ),
        pk=pk,
    )
    auto_parse = False
    if request.GET.get("auto_parse") == "1" and request.session.get("auto_parse_note_id") == note.pk:
        del request.session["auto_parse_note_id"]
        auto_parse = note.parse_status == MeetingNote.ParseStatus.NOT_PARSED
    return render(request, "core/meeting_detail.html", {
        "note": note,
        "latest_draft": note.ordered_drafts[0] if note.ordered_drafts else None,
        "confirmed_draft": next((draft for draft in note.ordered_drafts if draft.confirmed_at), None),
        "auto_parse": auto_parse,
    })


@login_required
def meeting_list(request):
    meeting_list = list(MeetingNote.objects.prefetch_related(
        Prefetch("drafts", queryset=ImportDraft.objects.order_by("-created_at", "-pk"), to_attr="ordered_drafts")
    ))
    for note in meeting_list:
        note.latest_draft = note.ordered_drafts[0] if note.ordered_drafts else None
    if request.GET.get("state") == "pending":
        meeting_list = [note for note in meeting_list
                        if note.parse_status == MeetingNote.ParseStatus.SUCCESS
                        and note.latest_draft and not note.latest_draft.confirmed_at]
    return render(request, "core/meeting_list.html", {"meeting_list": meeting_list})


@login_required
def meeting_parse(request, pk):
    note = get_object_or_404(MeetingNote, pk=pk)
    if request.method != "POST":
        return redirect("meeting_detail", pk=pk)
    wants_json = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    try:
        if note.parse_status == MeetingNote.ParseStatus.IMPORTED:
            raise LLMParseError("该会议已入库，请查看已入库结果；如有新的进展，请新建会议记录。")
        draft = parse_meeting_note(note)
    except LLMParseError as exc:
        if wants_json:
            return JsonResponse({"ok": False, "message": str(exc)}, status=422)
        messages.error(request, str(exc))
        return redirect("meeting_detail", pk=pk)
    if wants_json:
        return JsonResponse({"ok": True, "redirect_url": reverse("draft_review", args=[draft.pk])})
    return redirect("draft_review", pk=draft.pk)


def _draft_context(draft, error="", decisions=None):
    context = build_draft_review(draft, decisions=decisions)
    baseline = {"draft_id": draft.pk, "tasks": [
        {"values": {field: row["item"].get(field) for field in TASK_DEFAULTS},
         "provided": sorted(task_provided_fields(item))}
        for item, row in zip(draft.payload.get("tasks", []), context["task_rows"], strict=True)
    ]}
    return {
        **context,
        "review_baseline": signing.dumps(baseline, salt="draft-review", compress=True),
        "draft": draft,
        "note": draft.meeting_note,
        "error": error,
        "is_confirmed": bool(draft.confirmed_at),
    }


@login_required
def draft_review(request, pk):
    draft = get_object_or_404(ImportDraft.objects.select_related("meeting_note"), pk=pk)
    return render(request, "core/draft_review.html", _draft_context(draft))


@login_required
def draft_confirm(request, pk):
    draft = get_object_or_404(ImportDraft.objects.select_related("meeting_note"), pk=pk)
    if request.method != "POST":
        return redirect("draft_review", pk=pk)
    if draft.confirmed_at:
        return render(
            request,
            "core/draft_review.html",
            _draft_context(draft, "该草稿已经入库，内容只读，不能再次提交。"),
        )
    payload = deepcopy(draft.payload)
    review_rows = build_draft_review(draft)["task_rows"]
    signed_rows = None
    if request.POST.get("review_baseline"):
        try:
            baseline = signing.loads(request.POST["review_baseline"], salt="draft-review")
            if baseline["draft_id"] != draft.pk or len(baseline["tasks"]) != len(review_rows):
                raise signing.BadSignature("草稿不匹配")
            signed_rows = baseline["tasks"]
        except signing.BadSignature:
            return HttpResponse("审阅表单已失效，请重新打开草稿。", status=400)
    decisions = {"tasks": [], "risks": [], "milestones": []}
    for index, item in enumerate(payload.get("tasks", [])):
        edited = item.copy()
        provided = task_provided_fields(item)
        baseline = review_rows[index]["item"]
        if signed_rows is not None:
            # Compare with the values actually rendered, including an error redisplay.
            # Preserved values from another selected task must not become user edits.
            baseline = {**baseline, **signed_rows[index]["values"]}
            provided.update(signed_rows[index]["provided"])
        for field in ("title", "description", "status", "priority", "current_note", "completed_work", "next_step", "planned_start_date", "due_date", "acceptance_date"):
            if f"task_{index}_{field}" in request.POST:
                value = request.POST.get(f"task_{index}_{field}", "")
                edited[field] = value or None if field.endswith("_date") else value
                if edited[field] != baseline.get(field):
                    provided.add(field)
        if f"task_{index}_progress" in request.POST:
            try: edited["progress"] = int(request.POST[f"task_{index}_progress"])
            except ValueError: edited["progress"] = -1
            if edited["progress"] != baseline.get("progress"):
                provided.add("progress")
        edited["_provided_fields"] = sorted(provided)
        payload["tasks"][index] = edited
        decisions["tasks"].append({
            "action": request.POST.get(f"task_{index}_action"),
            "project_id": request.POST.get(f"task_{index}_project") or None,
            "assignee_id": request.POST.get(f"task_{index}_assignee") or None,
            "task_id": request.POST.get(f"task_{index}_existing") or None,
        })
    for kind in ("risks", "milestones"):
        for index, item in enumerate(payload.get(kind, [])):
            edited = item.copy()
            date_field = "due_date" if kind == "risks" else "target_date"
            posted_date = f"{kind}_{index}_{date_field}"
            if posted_date in request.POST:
                edited[date_field] = request.POST.get(posted_date) or None
            payload[kind][index] = edited
            row = {"action": request.POST.get(f"{kind}_{index}_action"), "project_id": request.POST.get(f"{kind}_{index}_project") or None}
            if kind == "risks": row["owner_id"] = request.POST.get(f"risks_{index}_owner") or None
            decisions[kind].append(row)
    try:
        result = confirm_draft(draft.pk, decisions, payload=payload)
    except (DraftConfirmationError, ValueError) as exc:
        draft.payload = payload
        return render(
            request,
            "core/draft_review.html",
            _draft_context(draft, f"请修正：{exc}", decisions=decisions),
            status=200,
        )
    messages.success(request, f"已入库：新建 {result.created_tasks} 个任务，更新 {result.updated_tasks} 个任务。")
    return redirect("meeting_detail", pk=draft.meeting_note_id)


@login_required
def task_list(request):
    tasks = Task.objects.select_related("project", "assignee")
    filters = {}
    query = request.GET.get("q", "").strip()
    filters["q"] = query
    if query:
        tasks = tasks.filter(title__icontains=query)
    for field in ("project", "assignee", "status", "priority"):
        value = request.GET.get(field, "")
        filters[field] = value
        if value: tasks = tasks.filter(**{field: value})
    queue = request.GET.get("queue", "")
    filters["queue"] = queue
    today = timezone.localdate()
    if queue == "overdue":
        tasks = tasks.exclude(status=Task.Status.DONE).filter(due_date__lt=today)
    elif queue == "due_soon":
        tasks = tasks.exclude(status=Task.Status.DONE).filter(due_date__gte=today, due_date__lte=today + timedelta(days=7))
    elif queue == "stale":
        tasks = tasks.exclude(status=Task.Status.DONE).filter(updated_at__date__lt=today - timedelta(days=7))
    return render(request, "core/task_list.html", {
        "tasks": tasks,
        "projects": Project.objects.all(),
        "people": Person.objects.filter(is_active=True),
        "statuses": Task.Status.choices,
        "priorities": Task.Priority.choices,
        "filters": filters,
    })


@login_required
def task_board(request):
    tasks = Task.objects.select_related("project", "assignee")
    columns = [(value, label, tasks.filter(status=value)) for value, label in Task.Status.choices]
    return render(request, "core/task_board.html", {"columns": columns})


def _task_detail_context(task, progress_form=None):
    progress_updates = list(task.progress_updates.select_related("meeting_note").order_by("-recorded_at", "-pk"))
    status_labels = dict(Task.Status.choices)
    for update in progress_updates:
        update.new_status_label = status_labels.get(update.new_status, update.new_status or "未设置")
    return {
        "task": task,
        "progress_form": progress_form or TaskProgressForm(task=task),
        "related_risks": task.risks.select_related("owner").all(),
        "progress_updates": progress_updates,
    }


@login_required
def task_detail(request, pk):
    task = get_object_or_404(Task.objects.select_related("project", "assignee", "source_meeting"), pk=pk)
    return render(request, "core/task_detail.html", _task_detail_context(task))


@login_required
def task_progress_update(request, pk):
    task = get_object_or_404(Task.objects.select_related("project", "assignee", "source_meeting"), pk=pk)
    if request.method != "POST":
        return redirect("task_detail", pk=task.pk)
    form = TaskProgressForm(request.POST, task=task)
    if not form.is_valid():
        return render(request, "core/task_detail.html", _task_detail_context(task, form))
    record_task_progress(task, form.cleaned_data)
    messages.success(request, "已记录本次进展。")
    return redirect("task_detail", pk=task.pk)


@login_required
def task_edit(request, pk=None):
    task = get_object_or_404(Task, pk=pk) if pk else None
    old_progress, old_status = (task.progress, task.status) if task else (None, "")
    form = TaskForm(request.POST or None, instance=task)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            task = form.save(commit=False)
            sync_task_completion_timestamp(task, old_status)
            task.save()
            form.save_m2m()
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
    default_start = today - timedelta(days=today.weekday())
    start = request.GET.get("start") or default_start.isoformat()
    end = request.GET.get("end") or today.isoformat()
    error = ""
    try:
        start_date, end_date = date.fromisoformat(start), date.fromisoformat(end)
        if start_date > end_date:
            raise ValueError
    except ValueError:
        start_date, end_date = default_start, today
        start, end = start_date.isoformat(), end_date.isoformat()
        error = "日期格式无效，已恢复为本周范围。"
    report = build_weekly_report(start_date, end_date)
    return render(request, "core/report.html", {"start": start, "end": end, "markdown": report.markdown, "error": error})


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
