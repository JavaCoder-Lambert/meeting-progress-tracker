from datetime import date, timedelta
from urllib.parse import urlencode
import re

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Exists, OuterRef, Prefetch, Q, Subquery
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from .forms import MeetingNoteForm, PersonForm, ProjectForm, TaskForm, TaskProgressForm
from .models import ImportDraft, MeetingNote, ParseJob, Person, Project, Task
from .services.dashboard import dashboard_context
from .services.draft_confirmation import DraftConfirmationError, confirm_draft
from .services.exports import build_csv_zip, build_json_export
from .services.llm_client import LLMParseError
from .services.parse_jobs import enqueue_parse, recover_expired_jobs
from .services.reports import build_weekly_report
from .services.draft_review import build_draft_review
from .services.draft_stash import (ReviewConflict, check_editable, check_review_version,
    persist_review, read_review_submission, reference_target, review_snapshot)
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
        capture_token = request.POST.get("capture_draft_token", "")
        if re.fullmatch(r"[A-Za-z0-9_-]{1,100}", capture_token):
            request.session["capture_saved"] = {"note_id": note.pk, "user_id": request.user.pk, "token": capture_token}
        if form.cleaned_data.get("intent") == "parse":
            try:
                enqueue_parse(note)
            except LLMParseError as exc:
                messages.error(request, str(exc))
        return redirect("meeting_detail", pk=note.pk)
    return render(request, "core/meeting_form.html", {"form": form})


def _meeting_notes():
    drafts = ImportDraft.objects.filter(meeting_note_id=OuterRef("pk")).order_by("-created_at", "-pk")
    return MeetingNote.objects.annotate(
        latest_draft_id=Subquery(drafts.values("pk")[:1]),
        latest_review_saved_at=Subquery(drafts.values("review_saved_at")[:1]),
        confirmed_draft_id=Subquery(drafts.filter(confirmed_at__isnull=False).values("pk")[:1]),
        has_confirmed=Exists(drafts.filter(confirmed_at__isnull=False)),
    )


def _parse_state(note):
    result = {"ok": True, "state": note.parse_status, "message": "", "redirect_url": None,
              "status_url": reverse("meeting_parse_status", args=[note.pk]), "started_at": None}
    if note.has_confirmed or note.parse_status == MeetingNote.ParseStatus.IMPORTED:
        result["state"] = "imported"
        if note.confirmed_draft_id:
            result["redirect_url"] = reverse("draft_review", args=[note.confirmed_draft_id])
    elif note.parse_status == MeetingNote.ParseStatus.SUCCESS and note.latest_draft_id:
        result["state"] = "success"
        result["redirect_url"] = reverse("draft_review", args=[note.latest_draft_id])
    else:
        job = ParseJob.objects.filter(meeting_note_id=note.pk).only("status", "queued_at", "started_at", "error").first()
        if job and job.status in (ParseJob.Status.QUEUED, ParseJob.Status.RUNNING):
            result.update(state=job.status, started_at=(job.started_at or job.queued_at).isoformat())
            result["message"] = "已加入解析队列，可以离开页面。" if job.status == ParseJob.Status.QUEUED else "正在整理会议内容，可以离开页面，稍后回来查看。"
        elif note.parse_status in (MeetingNote.ParseStatus.FAILED, MeetingNote.ParseStatus.PARSING):
            result.update(state="failed", message=note.parse_error or "上次解析已中断，记录已保存。请点击重试。")
    return result


@login_required
def meeting_detail(request, pk):
    recover_expired_jobs()
    note = get_object_or_404(_meeting_notes().defer("raw_llm_response"), pk=pk)
    state = _parse_state(note)
    capture = request.session.get("capture_saved", {})
    capture_token = ""
    if capture.get("note_id") == pk and capture.get("user_id") == request.user.pk:
        capture_token = capture.get("token", "")
        request.session.pop("capture_saved", None)
    return render(request, "core/meeting_detail.html", {
        "note": note,
        "capture_saved_token": capture_token,
        "latest_draft": ImportDraft.objects.only("pk", "confirmed_at").filter(pk=note.latest_draft_id).first(),
        "confirmed_draft": ImportDraft.objects.only("pk", "confirmed_at").filter(pk=note.confirmed_draft_id).first(),
        "auto_parse": False,
        "parse_state": state,
        "parse_active": state["state"] in ("queued", "running"),
    })


@login_required
def meeting_list(request):
    notes = _meeting_notes().defer("raw_text", "raw_llm_response", "parse_error").order_by("-meeting_date", "-pk")
    if request.GET.get("state") == "pending":
        notes = notes.filter(parse_status=MeetingNote.ParseStatus.SUCCESS, has_confirmed=False, latest_draft_id__isnull=False)
    page = Paginator(notes, 20).get_page(request.GET.get("page"))
    return render(request, "core/meeting_list.html", {"meeting_list": page, "page_obj": page,
                  "state_filter": request.GET.get("state", "")})


@login_required
def meeting_parse(request, pk):
    note = get_object_or_404(MeetingNote, pk=pk)
    if request.method != "POST":
        return redirect("meeting_detail", pk=pk)
    wants_json = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    try:
        enqueue_parse(note)
    except LLMParseError as exc:
        if wants_json:
            return JsonResponse({"ok": False, "message": str(exc)}, status=422)
        messages.error(request, str(exc))
        return redirect("meeting_detail", pk=pk)
    if wants_json:
        return JsonResponse(_parse_state(_meeting_notes().get(pk=pk)), status=202)
    messages.success(request, "已加入解析队列，可以离开页面，稍后回来查看结果。")
    return redirect("meeting_detail", pk=pk)


@login_required
@require_GET
def meeting_parse_status(request, pk):
    recover_expired_jobs()
    note = get_object_or_404(_meeting_notes().defer("raw_text", "raw_llm_response"), pk=pk)
    return JsonResponse(_parse_state(note))


def _draft_context(draft, error="", decisions=None):
    if decisions is None:
        draft, decisions = review_snapshot(draft)
    context = build_draft_review(draft, decisions=decisions, preserve_values=bool(decisions))
    baseline = {"draft_id": draft.pk, "version": draft.review_version, "tasks": [
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
    context = _draft_context(draft)
    focus = request.GET.get("focus", "")
    if focus.isdecimal() and len(focus) < 6 and int(focus) < len(context["task_rows"]):
        context["task_rows"][int(focus)]["focused"] = True
    return render(request, "core/draft_review.html", context)


def _blocked_review_response(request, draft, error, status):
    try:
        payload, decisions = read_review_submission(draft, request.POST)
    except signing.BadSignature as exc:
        return HttpResponse(str(exc), status=400)
    draft.payload = payload
    context = _draft_context(draft, str(error), decisions=decisions)
    context["review_blocked"] = True
    context["is_confirmed"] = False  # Display the rejected submission, not an import record.
    return render(request, "core/draft_review.html", context, status=status)


@login_required
@require_POST
@transaction.atomic
def draft_save(request, pk):
    draft = get_object_or_404(ImportDraft.objects.select_for_update().select_related("meeting_note"), pk=pk)
    wants_json = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    try:
        check_editable(draft)
        check_review_version(draft, request.POST.get("review_version"))
        destination = request.POST.get("destination", "stay")
        target = reference_target(draft, destination) if destination != "stay" else None
        payload, decisions = read_review_submission(draft, request.POST)
    except (ReviewConflict, ValueError, signing.BadSignature) as exc:
        status = 409 if isinstance(exc, ReviewConflict) else 400
        if wants_json:
            return JsonResponse({"ok": False, "message": str(exc)}, status=status)
        if isinstance(exc, ReviewConflict):
            return _blocked_review_response(request, draft, exc, status)
        return HttpResponse(str(exc), status=status)
    persist_review(draft, payload, decisions)
    if wants_json:
        return JsonResponse({"ok": True, "version": draft.review_version, "saved_at": draft.review_saved_at.isoformat()})
    if target:
        token = signing.dumps({**target, "draft_id": draft.pk, "version": draft.review_version}, salt="review-return")
        url = reverse("project_create" if target["kind"] == "project" else "person_create")
        return redirect(url + "?" + urlencode({"review": token}))
    messages.success(request, "审阅修改已暂存到服务器，尚未写入正式任务。")
    return redirect("draft_review", pk=pk)


@login_required
@transaction.atomic
def draft_confirm(request, pk):
    draft = get_object_or_404(ImportDraft.objects.select_for_update().select_related("meeting_note"), pk=pk)
    if request.method != "POST":
        return redirect("draft_review", pk=pk)
    if draft.confirmed_at:
        return _blocked_review_response(request, draft, "该草稿已经入库，不能再次提交；以下仅保留本次未写入的内容。", 200)
    try:
        payload, decisions = read_review_submission(draft, request.POST)
        signed_version = signing.loads(request.POST["review_baseline"], salt="draft-review").get("version", 0) if request.POST.get("review_baseline") else 0
        check_review_version(draft, request.POST.get("review_version", signed_version))
    except ReviewConflict as exc:
        return _blocked_review_response(request, draft, exc, 409)
    except signing.BadSignature as exc:
        return HttpResponse(str(exc), status=400)
    try:
        result = confirm_draft(draft.pk, decisions, payload=payload)
    except (DraftConfirmationError, ValueError) as exc:
        try:
            check_editable(draft)
        except ReviewConflict:
            return _blocked_review_response(request, draft, exc, 200)
        else:
            persist_review(draft, payload, decisions)
        return render(
            request,
            "core/draft_review.html",
            _draft_context(draft, f"请修正：{exc}"),
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
        if value and (field not in {"project", "assignee"} or value.isdecimal() and len(value) < 12):
            tasks = tasks.filter(**{field: value})
    queue = request.GET.get("queue", "")
    filters["queue"] = queue
    today = timezone.localdate()
    if queue == "overdue":
        tasks = tasks.exclude(status=Task.Status.DONE).filter(due_date__lt=today)
    elif queue == "due_soon":
        tasks = tasks.exclude(status=Task.Status.DONE).filter(due_date__gte=today, due_date__lte=today + timedelta(days=7))
    elif queue == "stale":
        tasks = tasks.exclude(status=Task.Status.DONE).filter(updated_at__date__lt=today - timedelta(days=7))
    page = Paginator(tasks.order_by("due_date", "title", "pk"), 30).get_page(request.GET.get("page"))
    pagination_query = request.GET.copy()
    pagination_query.pop("page", None)
    return render(request, "core/task_list.html", {
        "tasks": page, "page_obj": page, "querystring": pagination_query.urlencode(),
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
    try:
        record_task_progress(task, form.cleaned_data)
    except ValidationError as exc:
        # The task can change after form validation; keep service-level errors
        # in the same form, including fields absent from this compact editor.
        for field, errors in getattr(exc, "message_dict", {"__all__": exc.messages}).items():
            form.add_error(field if field in form.fields else None, errors)
        task.refresh_from_db()
        return render(request, "core/task_detail.html", _task_detail_context(task, form))
    messages.success(request, "已记录本次进展。")
    return redirect("task_detail", pk=task.pk)


@login_required
def task_edit(request, pk=None):
    task = get_object_or_404(Task, pk=pk) if pk else None
    old_progress, old_status = (task.progress, task.status) if task else (None, "")
    project_id = request.GET.get("project", "")
    initial = {"project": project_id} if project_id.isdecimal() and len(project_id) < 12 else {}
    form = TaskForm(request.POST or None, instance=task, initial=initial)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            task = form.save(commit=False)
            sync_task_completion_timestamp(task, old_status)
            if task.pk and not set(form.changed_data).difference({"planned_for"}):
                # Replanning is not a progress update and must not reset the
                # reminder for work that has not received a real update.
                task.save(update_fields=["planned_for"])
            else:
                task.save()
            form.save_m2m()
            if old_progress != task.progress or old_status != task.status:
                task.progress_updates.create(previous_progress=old_progress, new_progress=task.progress, previous_status=old_status, new_status=task.status)
        return redirect("task_detail", pk=task.pk)
    return render(request, "core/form_page.html", {"form": form, "title": "编辑任务" if task else "新建任务"})


def _model_form_view(request, form_class, instance, title, redirect_name):
    return_context = None
    kind = "project" if form_class == ProjectForm else "person"
    token = request.GET.get("review", "") if instance is None else ""
    if token:
        try:
            return_context = signing.loads(token, salt="review-return")
            if return_context["kind"] != kind:
                raise signing.BadSignature
            get_object_or_404(ImportDraft, pk=return_context["draft_id"])
        except (signing.BadSignature, KeyError, TypeError):
            return HttpResponse("返回草稿的链接无效，请从草稿页面重新新建。", status=400)
    form = form_class(request.POST or None, instance=instance, initial={"name": return_context["name"]} if return_context else None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            created = form.save()
            if return_context:
                draft = get_object_or_404(ImportDraft.objects.select_for_update().select_related("meeting_note"), pk=return_context["draft_id"])
                try:
                    check_editable(draft)
                    check_review_version(draft, return_context["version"])
                except ReviewConflict:
                    messages.warning(request, "资料已创建；草稿已在其他页面更改，未覆盖新内容，请手动选择刚创建的资料。")
                else:
                    if return_context["group"]:
                        state = draft.review_state
                        state["decisions"][return_context["group"]][return_context["index"]][return_context["key"]] = str(created.pk)
                        persist_review(draft, state["payload"], state["decisions"])
                    messages.success(request, "已创建并返回草稿，之前的修改均已保留。")
                url = reverse("draft_review", args=[draft.pk])
                if return_context["group"] == "tasks":
                    index = return_context["index"]
                    url += f"?focus={index}#review-task-{index}"
                return redirect(url)
        return redirect(redirect_name)
    return render(request, "core/form_page.html", {"form": form, "title": title,
        "return_draft_id": return_context["draft_id"] if return_context else None})


@login_required
def project_list(request):
    projects = Project.objects.annotate(
        task_count=Count("tasks"), done_count=Count("tasks", filter=Q(tasks__status=Task.Status.DONE)),
    )
    if request.GET.get("archived") != "1":
        projects = projects.exclude(status=Project.Status.ARCHIVED)
    return render(request, "core/project_list.html", {"projects": projects})


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
