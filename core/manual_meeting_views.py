"""HTTP boundary for the manual workspace; all draft/application rules live in services."""

import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import IntegrityError, OperationalError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from .manual_meeting_forms import ManualMeetingForm
from .models import MeetingSession, Person, Project, Task
from .services.manual_meetings import (
    ManualMeetingError, confirm_session, create_session, preview_session,
    save_session, serialize_session, task_option,
)


def _session_data(session):
    data = serialize_session(session)
    data["urls"] = {
        key: reverse(name, args=[session.pk]) for key, name in (
            ("save", "manual_meeting_save"), ("preview", "manual_meeting_preview"),
            ("confirm", "manual_meeting_confirm"),
        )
    }
    data["urls"].update(reference=reverse("manual_meeting_reference"), list=reverse("meeting_list"))
    return data


def _json_body(request):
    if len(request.body) > 2_000_000:
        raise ManualMeetingError("会议内容过长，请拆分为多次会议。")
    try:
        data = json.loads(request.body)
    except (ValueError, UnicodeDecodeError):
        raise ManualMeetingError("请求内容无效，请保留当前页面后重试。")
    if not isinstance(data, dict):
        raise ManualMeetingError("请求格式无效。")
    return data


def _error_response(error):
    return JsonResponse({"ok": False, "error": str(error), "conflict": error.conflict},
                        status=409 if error.conflict else 400)


@login_required
@never_cache
def manual_meeting_create(request):
    form = ManualMeetingForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        try:
            session = create_session(form.meeting_state())
        except ManualMeetingError as exc:
            form.add_error(None, str(exc))
        else:
            return redirect("manual_meeting_workspace", pk=session.pk)
    return render(request, "core/manual_meeting_form.html", {"form": form})


@login_required
@require_GET
@never_cache
def manual_meeting_workspace(request, pk):
    session = get_object_or_404(MeetingSession.objects.select_related("meeting_note"), pk=pk)
    catalog = {"projects": [], "people": [], "tasks": [], "statuses": []}
    if session.confirmed_at is None:
        catalog = {
            "projects": list(Project.objects.order_by("name").values("id", "name")),
            "people": list(Person.objects.order_by("name").values("id", "name")),
            "tasks": [task_option(task) for task in Task.objects.select_related("project", "assignee").order_by("title", "pk")],
            "statuses": [{"value": value, "label": label} for value, label in Task.Status.choices],
        }
    return render(request, "core/manual_meeting_workspace.html", {
        "meeting_session": session, "session_data": _session_data(session), "catalog": catalog,
    })


@login_required
@require_POST
@never_cache
def manual_meeting_save(request, pk):
    get_object_or_404(MeetingSession.objects.only("pk"), pk=pk)
    try:
        body = _json_body(request)
        session = save_session(pk, body.get("version"), body.get("state"))
    except ManualMeetingError as exc:
        return _error_response(exc)
    except OperationalError as exc:
        if "locked" not in str(exc).lower():
            raise
        return JsonResponse({"ok": False, "error": "暂存服务繁忙，内容仍留在页面，请稍后重试。", "conflict": False}, status=503)
    return JsonResponse({"ok": True, **serialize_session(session)})


def _render_preview(request, session, *, error="", status=200):
    preview = preview_session(session)
    if error and error not in preview["errors"]:
        preview["errors"].insert(0, error)
    return render(request, "core/manual_meeting_preview.html", {
        "meeting_session": session, "preview": preview, "session_data": _session_data(session),
    }, status=status)


@login_required
@require_GET
@never_cache
def manual_meeting_preview(request, pk):
    session = get_object_or_404(MeetingSession.objects.select_related("meeting_note"), pk=pk)
    if session.confirmed_at:
        return redirect("manual_meeting_workspace", pk=pk)
    return _render_preview(request, session)


@login_required
@require_POST
@never_cache
def manual_meeting_confirm(request, pk):
    session = get_object_or_404(MeetingSession.objects.select_related("meeting_note"), pk=pk)
    try:
        confirm_session(pk, request.POST.get("version"))
    except ManualMeetingError as exc:
        session.refresh_from_db()
        return _render_preview(request, session, error=str(exc), status=409 if exc.conflict else 400)
    except OperationalError as exc:
        if "locked" not in str(exc).lower():
            raise
        return _render_preview(request, session, error="保存服务繁忙，尚未确认，请稍后重试。", status=503)
    messages.success(request, "会议已确认，纪要与进展已保存。")
    return redirect("manual_meeting_workspace", pk=pk)


@login_required
@require_POST
@never_cache
def manual_meeting_reference(request):
    try:
        body = _json_body(request)
        kind = body.get("kind")
        model = {"project": Project, "person": Person}.get(kind) if isinstance(kind, str) else None
        name = body.get("name")
        if model is None or not isinstance(name, str) or not name.strip():
            raise ManualMeetingError("请选择项目或人员，并填写名称。")
        name = name.strip()
        item = model(name=name)
        # Validate length/required values before creating. Existing exact names are reused.
        item.full_clean(validate_unique=False)
        item, _ = model.objects.get_or_create(name=name)
    except ValidationError as exc:
        return _error_response(ManualMeetingError("；".join(exc.messages)))
    except ManualMeetingError as exc:
        return _error_response(exc)
    except IntegrityError:
        return _error_response(ManualMeetingError("此名称已存在，请刷新候选项后选择。"))
    return JsonResponse({"ok": True, "kind": body["kind"], "item": {"id": item.pk, "name": item.name}})
