from datetime import date

import pytest
from django.urls import reverse
from django.utils import timezone

from core.models import ImportDraft, MeetingNote, Milestone, Project, Risk, Task
from core.services.llm_client import LLMParseError


@pytest.mark.django_db
def test_authenticated_user_can_save_raw_meeting(admin_client):
    response = admin_client.post("/meetings/new/", {"title": "周五进度会", "meeting_date": "2026-09-04", "raw_text": "张川：数据迁移完成"})
    assert response.status_code == 302
    assert MeetingNote.objects.get().raw_text == "张川：数据迁移完成"


@pytest.mark.django_db
def test_quick_capture_defaults_to_today(admin_client):
    response = admin_client.get(reverse("meeting_create"))

    assert response.context["form"]["meeting_date"].value() == timezone.localdate()


@pytest.mark.django_db
def test_meeting_detail_exposes_accessible_parse_progress(admin_client):
    note = MeetingNote.objects.create(title="周会", meeting_date=date(2026, 9, 4), raw_text="记录")

    response = admin_client.get(f"/meetings/{note.id}/")

    content = response.content.decode()
    assert 'data-parse-form' in content
    assert 'aria-live="polite"' in content
    assert "正在整理会议内容" in content


@pytest.mark.django_db
def test_ajax_parse_success_returns_draft_destination(admin_client, monkeypatch):
    note = MeetingNote.objects.create(title="周会", meeting_date=date(2026, 9, 4), raw_text="记录")
    draft = ImportDraft.objects.create(meeting_note=note, payload={"summary": "", "tasks": [], "risks": [], "milestones": [], "uncertainties": []})
    monkeypatch.setattr("core.views.parse_meeting_note", lambda parsed_note: draft)

    response = admin_client.post(
        f"/meetings/{note.id}/parse/",
        HTTP_X_REQUESTED_WITH="XMLHttpRequest",
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True, "redirect_url": f"/drafts/{draft.id}/"}


@pytest.mark.django_db
def test_ajax_parse_failure_returns_inline_error(admin_client, monkeypatch):
    note = MeetingNote.objects.create(title="周会", meeting_date=date(2026, 9, 4), raw_text="记录")

    def fail(_note):
        raise LLMParseError("模型返回内容格式不正确，请重试。")

    monkeypatch.setattr("core.views.parse_meeting_note", fail)

    response = admin_client.post(
        f"/meetings/{note.id}/parse/",
        HTTP_X_REQUESTED_WITH="XMLHttpRequest",
    )

    assert response.status_code == 422
    assert response.json() == {"ok": False, "message": "模型返回内容格式不正确，请重试。"}


@pytest.mark.django_db
def test_regular_parse_success_keeps_redirect_fallback(admin_client, monkeypatch):
    note = MeetingNote.objects.create(title="周会", meeting_date=date(2026, 9, 4), raw_text="记录")
    draft = ImportDraft.objects.create(meeting_note=note, payload={"summary": "", "tasks": [], "risks": [], "milestones": [], "uncertainties": []})
    monkeypatch.setattr("core.views.parse_meeting_note", lambda parsed_note: draft)

    response = admin_client.post(reverse("meeting_parse", args=[note.id]))

    assert response.status_code == 302
    assert response.url == reverse("draft_review", args=[draft.id])


@pytest.mark.django_db
def test_regular_parse_failure_keeps_message_fallback(admin_client, monkeypatch):
    note = MeetingNote.objects.create(title="周会", meeting_date=date(2026, 9, 4), raw_text="记录")

    def fail(_note):
        raise LLMParseError("用于页面展示的错误")

    monkeypatch.setattr("core.views.parse_meeting_note", fail)

    response = admin_client.post(reverse("meeting_parse", args=[note.id]), follow=True)

    assert response.status_code == 200
    assert response.redirect_chain[-1][0] == reverse("meeting_detail", args=[note.id])
    assert "用于页面展示的错误" in response.content.decode()


@pytest.mark.django_db
def test_confirm_validation_error_writes_nothing(admin_client):
    note = MeetingNote.objects.create(title="周会", meeting_date=date(2026, 9, 4), raw_text="记录", parse_status="success")
    draft = ImportDraft.objects.create(meeting_note=note, payload={"summary": "", "risks": [], "milestones": [], "uncertainties": [], "tasks": [{"title": "联调", "project_name": "", "assignee_name": "", "description": "", "planned_start_date": None, "due_date": None, "acceptance_date": None, "status": "in_progress", "priority": "normal", "progress": 20, "current_note": "", "completed_work": "", "next_step": ""}]})
    response = admin_client.post(f"/drafts/{draft.id}/confirm/", {"task_0_action": "create", "task_0_project": ""})
    assert response.status_code == 200
    assert "请修正" in response.content.decode()
    assert Task.objects.count() == 0


@pytest.mark.django_db
def test_confirmation_can_edit_task_title(admin_client):
    project = Project.objects.create(name="SKU")
    note = MeetingNote.objects.create(title="周会", meeting_date=date(2026, 9, 4), raw_text="记录", parse_status="success")
    draft = ImportDraft.objects.create(meeting_note=note, payload={"summary": "", "risks": [], "milestones": [], "uncertainties": [], "tasks": [{"title": "原标题", "project_name": "SKU", "assignee_name": "", "description": "", "planned_start_date": None, "due_date": None, "acceptance_date": None, "status": "in_progress", "priority": "normal", "progress": 20, "current_note": "", "completed_work": "", "next_step": ""}]})
    response = admin_client.post(f"/drafts/{draft.id}/confirm/", {"task_0_action": "create", "task_0_project": project.id, "task_0_title": "人工修正标题", "task_0_progress": "30", "task_0_status": "in_progress"})
    assert response.status_code == 302
    assert Task.objects.get().title == "人工修正标题"


@pytest.mark.django_db
def test_draft_review_offers_all_task_statuses(admin_client):
    note = MeetingNote.objects.create(title="周会", meeting_date=date(2026, 9, 4), raw_text="记录")
    draft = ImportDraft.objects.create(meeting_note=note, payload={"summary": "", "risks": [], "milestones": [], "uncertainties": [], "tasks": [{"title": "联调", "project_name": "", "assignee_name": "", "description": "", "planned_start_date": None, "due_date": None, "acceptance_date": None, "status": "in_progress", "priority": "normal", "progress": 20, "current_note": "", "completed_work": "", "next_step": ""}]})
    response = admin_client.get(f"/drafts/{draft.id}/")
    content = response.content.decode()
    assert "未开始" in content
    assert "待验收" in content


@pytest.mark.django_db
def test_confirmation_error_preserves_update_decision_and_selection(admin_client):
    project = Project.objects.create(name="SKU")
    existing = Task.objects.create(project=project, title="联调")
    note = MeetingNote.objects.create(title="周会", meeting_date=date(2026, 9, 4), raw_text="记录", parse_status="success")
    draft = ImportDraft.objects.create(meeting_note=note, payload={
        "summary": "", "risks": [], "milestones": [], "uncertainties": [],
        "tasks": [{"title": "联调", "project_name": "SKU", "status": "in_progress", "progress": 20}],
    })

    response = admin_client.post(reverse("draft_confirm", args=[draft.id]), {
        "task_0_action": "update",
        "task_0_existing": existing.id,
        "task_0_project": "",
        "task_0_title": "人工修正后的联调",
        "task_0_status": "in_progress",
        "task_0_priority": "high",
        "task_0_progress": "30",
    })

    content = response.content.decode()
    assert response.status_code == 200
    assert 'value="update" selected' in content
    assert f'value="{existing.id}" selected' in content
    assert 'value="high" selected' in content
    assert 'value="人工修正后的联调"' in content
    assert Task.objects.get(pk=existing.pk).title == "联调"


@pytest.mark.django_db
def test_confirmed_draft_is_read_only_and_post_does_not_mutate_payload(admin_client):
    note = MeetingNote.objects.create(title="周会", meeting_date=date(2026, 9, 4), raw_text="记录")
    payload = {"summary": "", "risks": [], "milestones": [], "uncertainties": [], "tasks": [{"title": "确认前"}]}
    draft = ImportDraft.objects.create(meeting_note=note, payload=payload, confirmed_at=timezone.now())

    get_response = admin_client.get(reverse("draft_review", args=[draft.id]))
    post_response = admin_client.post(reverse("draft_confirm", args=[draft.id]), {
        "task_0_action": "ignore",
        "task_0_title": "不应保存",
    })

    draft.refresh_from_db()
    assert "该草稿已经入库" in get_response.content.decode()
    assert "确认并入库" not in get_response.content.decode()
    assert post_response.status_code == 200
    assert draft.payload == payload


@pytest.mark.django_db
def test_review_normalizes_dates_and_exposes_all_destructive_update_fields(admin_client):
    note = MeetingNote.objects.create(title="周会", meeting_date=date(2026, 9, 4), raw_text="记录")
    draft = ImportDraft.objects.create(meeting_note=note, payload={
        "summary": "", "uncertainties": [],
        "tasks": [{
            "title": "联调", "priority": "high", "planned_start_date": "0907",
            "due_date": "下周一", "acceptance_date": "2026年9月16日",
        }],
        "risks": [{"content": "字段尚未确认", "due_date": "9月8日"}],
        "milestones": [{"name": "产品验收", "target_date": "0916"}],
    })

    response = admin_client.get(reverse("draft_review", args=[draft.id]))
    content = response.content.decode()

    assert 'name="task_0_priority"' in content
    assert 'name="task_0_planned_start_date" value="2026-09-07"' in content
    assert 'type="text" name="task_0_due_date" value="下周一"' in content
    assert 'name="task_0_acceptance_date" value="2026-09-16"' in content
    assert 'name="risks_0_due_date" value="2026-09-08"' in content
    assert 'name="milestones_0_target_date" value="2026-09-16"' in content


@pytest.mark.django_db
def test_review_allows_editing_risk_and_milestone_dates_before_import(admin_client):
    project = Project.objects.create(name="SKU")
    note = MeetingNote.objects.create(title="周会", meeting_date=date(2026, 9, 4), raw_text="记录", parse_status="success")
    draft = ImportDraft.objects.create(meeting_note=note, payload={
        "summary": "", "uncertainties": [], "tasks": [],
        "risks": [{"content": "字段尚未确认", "due_date": "9月8日"}],
        "milestones": [{"name": "产品验收", "target_date": "0916"}],
    })

    response = admin_client.post(reverse("draft_confirm", args=[draft.id]), {
        "risks_0_action": "create",
        "risks_0_project": project.id,
        "risks_0_due_date": "2026-09-10",
        "milestones_0_action": "create",
        "milestones_0_project": project.id,
        "milestones_0_target_date": "2026-09-18",
    })

    assert response.status_code == 302
    assert Risk.objects.get().due_date == date(2026, 9, 10)
    assert Milestone.objects.get().target_date == date(2026, 9, 18)
