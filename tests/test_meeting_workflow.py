from datetime import date

import pytest

from core.models import ImportDraft, MeetingNote, Project, Task


@pytest.mark.django_db
def test_authenticated_user_can_save_raw_meeting(admin_client):
    response = admin_client.post("/meetings/new/", {"title": "周五进度会", "meeting_date": "2026-09-04", "raw_text": "张川：数据迁移完成"})
    assert response.status_code == 302
    assert MeetingNote.objects.get().raw_text == "张川：数据迁移完成"


@pytest.mark.django_db
def test_confirm_validation_error_writes_nothing(admin_client):
    note = MeetingNote.objects.create(title="周会", meeting_date=date(2026, 9, 4), raw_text="记录")
    draft = ImportDraft.objects.create(meeting_note=note, payload={"summary": "", "risks": [], "milestones": [], "uncertainties": [], "tasks": [{"title": "联调", "project_name": "", "assignee_name": "", "description": "", "planned_start_date": None, "due_date": None, "acceptance_date": None, "status": "in_progress", "priority": "normal", "progress": 20, "current_note": "", "completed_work": "", "next_step": ""}]})
    response = admin_client.post(f"/drafts/{draft.id}/confirm/", {"task_0_action": "create", "task_0_project": ""})
    assert response.status_code == 200
    assert "请修正" in response.content.decode()
    assert Task.objects.count() == 0


@pytest.mark.django_db
def test_confirmation_can_edit_task_title(admin_client):
    project = Project.objects.create(name="SKU")
    note = MeetingNote.objects.create(title="周会", meeting_date=date(2026, 9, 4), raw_text="记录")
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
