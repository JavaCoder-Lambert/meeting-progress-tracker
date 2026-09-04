from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render

from .forms import MeetingNoteForm
from .models import ImportDraft, MeetingNote, Person, Project, Task
from .services.draft_confirmation import DraftConfirmationError, confirm_draft
from .services.llm_client import LLMParseError, parse_meeting_note
from .services.task_matching import find_task_candidates


def health(request):
    return JsonResponse({"status": "ok"})


@login_required
def dashboard(request):
    return render(request, "core/dashboard.html")


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
