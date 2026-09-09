"""Small native forms for risk followup drafts and explicitly reviewed corrections."""
from copy import deepcopy
from uuid import uuid4
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.core.paginator import Paginator
from django.db import OperationalError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods
from core.models import MeetingNote, MeetingSession, Person, ProgressUpdate, Project, Risk, Task
from core.history_models import MeetingCorrection, RiskFollowup
from .services.manual_meetings import ManualMeetingError, save_session
from .services.meeting_corrections import correction_baseline, confirm_correction, effective_progress_updates, update_values
from .services.risk_followups import risk_baseline


class CorrectionForm(forms.Form):
    reason = forms.CharField(label="更正原因", widget=forms.Textarea(attrs={"rows":3}), max_length=20000)
    new_status = forms.ChoiceField(label="拟更正状态", choices=Task.Status.choices)
    new_progress = forms.IntegerField(label="拟更正进度", min_value=0, max_value=100)
    completed_work = forms.CharField(label="拟更正本次完成", required=False, widget=forms.Textarea(attrs={"rows":3}), max_length=20000)
    next_step = forms.CharField(label="拟更正下一步", required=False, widget=forms.Textarea(attrs={"rows":2}), max_length=20000)
    apply_current = forms.BooleanField(label="同时更新当前任务（仅今日且无后续进展时）", required=False)
    baseline = forms.CharField(widget=forms.HiddenInput)
    token = forms.CharField(widget=forms.HiddenInput, max_length=100)


class FollowupForm(forms.Form):
    risk = forms.ModelChoiceField(label="已有风险 / 待决策", queryset=Risk.objects.none())
    response = forms.CharField(label="本次答复", widget=forms.Textarea(attrs={"rows":3}), max_length=20000)
    next_step = forms.CharField(label="下一步", widget=forms.Textarea(attrs={"rows":2}), required=False, max_length=20000)
    status = forms.ChoiceField(label="本次状态", choices=Risk.Status.choices)
    owner = forms.ModelChoiceField(label="负责人", queryset=Person.objects.all(), required=False)
    due_date = forms.DateField(label="目标日期", required=False, widget=forms.DateInput(attrs={"type":"date"}))
    reason = forms.CharField(label="关闭 / 重开理由", widget=forms.Textarea(attrs={"rows":2}), required=False, max_length=20000)
    baseline = forms.CharField(widget=forms.HiddenInput)
    token = forms.CharField(widget=forms.HiddenInput, max_length=100)
    version = forms.IntegerField(widget=forms.HiddenInput, min_value=0)


def status_label(value):
    return dict(Task.Status.choices).get(value, "未记录")


def correction_labels(corrections):
    for item in corrections:
        item.original_status_label = status_label(item.original.get("new_status"))
        item.current_status_label = status_label(item.current.get("status"))
        item.proposed_status_label = status_label(item.proposed.get("new_status"))
    return corrections


@login_required
@never_cache
@require_http_methods(["GET"])
def meeting_history(request, pk):
    note = get_object_or_404(MeetingNote, pk=pk)
    updates = Paginator(note.progress_updates.select_related("task").order_by("pk"), 30).get_page(request.GET.get("page"))
    for update in updates:
        update.new_status_label = status_label(update.new_status)
    return render(request, "core/history_meeting.html", {"note":note,
        "updates":updates,
        "corrections":correction_labels(note.corrections.select_related("progress_update__task")),
        "followups":note.risk_followups.select_related("risk")})


@login_required
@never_cache
@require_http_methods(["GET"])
def project_history(request, pk):
    project = get_object_or_404(Project, pk=pk)
    return render(request, "core/history_meeting.html", {"project":project,
        "followups":Paginator(RiskFollowup.objects.filter(risk__project=project).select_related("risk", "meeting_note"),30).get_page(request.GET.get("page")),
        "corrections":correction_labels(MeetingCorrection.objects.filter(progress_update__task__project=project).select_related("progress_update__task", "meeting_note")[:100])})


@login_required
@never_cache
@require_http_methods(["GET", "POST"])
def progress_correction(request, pk):
    update = get_object_or_404(ProgressUpdate.objects.select_related("task__project", "task__assignee", "meeting_note"), pk=pk, meeting_note__isnull=False)
    effective = effective_progress_updates([update])[0]
    initial = {**update_values(effective), "baseline":correction_baseline(update), "token":str(uuid4())}
    form = CorrectionForm(request.POST or None, initial=initial)
    preview = None; status = 200
    if request.method == "POST":
        try:
            if request.POST.get("confirmation"):
                try:
                    payload = signing.loads(request.POST["confirmation"], salt="correction-confirm", max_age=3600)
                    if payload.pop("update_id") != pk:
                        raise signing.BadSignature
                except (signing.BadSignature, KeyError, TypeError) as exc:
                    raise ManualMeetingError("核对已失效，请重新填写更正。") from exc
                form = CorrectionForm({**payload["proposed"], **{key:payload[key] for key in ("reason", "baseline", "token", "apply_current")}})
                form.is_valid()
                confirm_correction(pk, **payload)
                messages.success(request, "更正已确认；原进展和原纪要保留。")
                return redirect("meeting_history", pk=update.meeting_note_id)
            if form.is_valid():
                data = form.cleaned_data
                preview = {"update_id":pk, "reason":data["reason"], "baseline":data["baseline"], "token":data["token"],
                    "apply_current":data["apply_current"], "proposed":{key:data[key] for key in update_values(update)}}
                if preview["proposed"]["new_status"] == Task.Status.DONE:
                    preview["proposed"]["new_progress"] = 100
        except ManualMeetingError as exc:
            form.add_error(None, str(exc)); status = 409 if exc.conflict else 400
        except OperationalError as exc:
            if "locked" not in str(exc).lower():
                raise
            form.add_error(None, "保存繁忙，尚未确认，请稍后重试。"); status = 503
    return render(request, "core/history_correction.html", {"update":update, "form":form,
        "original":update_values(update), "effective":update_values(effective), "preview":preview,
        "original_status_label":status_label(update.new_status), "effective_status_label":status_label(effective.new_status),
        "proposed_status_label":status_label(preview["proposed"]["new_status"]) if preview else "",
        "confirmation":signing.dumps(preview, salt="correction-confirm", compress=True) if preview else ""}, status=status)


@login_required
@never_cache
@require_http_methods(["GET", "POST"])
def session_followups(request, pk):
    session = get_object_or_404(MeetingSession.objects.select_related("meeting_note"), pk=pk, confirmed_at__isnull=True)
    risks = Risk.objects.select_related("project", "owner").order_by("project__name", "pk")
    if session.state.get("project_ids"):
        risks = risks.filter(project_id__in=session.state["project_ids"])
    selected_id = request.POST.get("risk") or request.GET.get("risk")
    selected = risks.filter(pk=selected_id).first() if str(selected_id).isdigit() else None
    saved = next((item for item in session.state.get("risk_followups", []) if selected and item["risk_id"] == selected.pk), None)
    initial = {"version":session.version, "token":str(uuid4())}
    if selected:
        initial.update(risk=selected, status=selected.status, owner=selected.owner_id, due_date=selected.due_date, baseline=risk_baseline(selected))
    if saved:
        initial.update(saved, owner=saved["owner_id"])
        if request.GET.get("refresh") == "1":
            initial["baseline"] = risk_baseline(selected)
    form = FollowupForm(request.POST or None, initial=initial)
    form.fields["risk"].queryset = risks.filter(pk=selected.pk) if selected else risks.none()
    form.fields["risk"].label_from_instance = lambda risk: f"{risk.project.name} · {risk.content}"
    status = 200
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        entry = {key:data[key] for key in ("response", "next_step", "reason", "status", "baseline", "token")}
        entry.update(risk_id=data["risk"].pk, owner_id=data["owner"].pk if data["owner"] else None,
            due_date=data["due_date"].isoformat() if data["due_date"] else "")
        state = deepcopy(session.state)
        state["risk_followups"] = [item for item in state.get("risk_followups", []) if item["risk_id"] != entry["risk_id"]] + [entry]
        try:
            saved_session = save_session(pk, data["version"], state)
        except ManualMeetingError as exc:
            form.add_error(None, str(exc)); status = 409 if exc.conflict else 400
        except OperationalError as exc:
            if "locked" not in str(exc).lower():
                raise
            form.add_error(None, "暂存繁忙，答复仍在页面，请稍后重试。"); status = 503
        else:
            if "application/json" in request.headers.get("Accept", ""):
                return JsonResponse({"ok":True, "version":saved_session.version,
                    "state":{key:value for key,value in request.POST.items() if key != "version"}})
            messages.success(request, "风险答复已暂存，结束会议时统一核对确认。")
            return redirect("session_followups", pk=pk)
    if request.method == "POST" and "application/json" in request.headers.get("Accept", ""):
        return JsonResponse({"ok":False, "error":"；".join(str(error) for errors in form.errors.values() for error in errors)},
            status=status if status != 200 else 400)
    choices = risks if request.GET.get("closed") else risks.exclude(status__in=[Risk.Status.CLOSED, Risk.Status.RESOLVED])
    return render(request, "core/history_followup.html", {"session":session, "form":form, "selected":selected,
        "risks":choices, "saved":session.state.get("risk_followups", [])}, status=status)
