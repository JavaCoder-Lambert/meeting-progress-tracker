from datetime import date

import pytest

from core.models import ImportDraft, MeetingNote, Person, Project, Task
from core.services.draft_confirmation import DraftAlreadyConfirmed, DraftConfirmationError, confirm_draft


@pytest.fixture
def draft(db):
    note = MeetingNote.objects.create(title="周会", meeting_date=date(2026, 9, 4), raw_text="原始记录")
    return ImportDraft.objects.create(meeting_note=note, payload={
        "summary": "", "uncertainties": [], "risks": [], "milestones": [],
        "tasks": [{"title": "发货仓库优先级逻辑调整", "project_name": "SKU改造", "assignee_name": "张川", "description": "", "planned_start_date": None, "due_date": "2026-09-09", "acceptance_date": "2026-09-16", "status": "in_progress", "priority": "normal", "progress": 50, "current_note": "开发中", "completed_work": "完成梳理", "next_step": "开发"}],
    })


@pytest.mark.django_db
def test_confirm_creates_records_and_progress_history(draft):
    project = Project.objects.create(name="SKU改造")
    person = Person.objects.create(name="张川")
    result = confirm_draft(draft.id, {"tasks": [{"action": "create", "project_id": project.id, "assignee_id": person.id}]})
    task = Task.objects.get(title="发货仓库优先级逻辑调整")
    assert result.created_tasks == 1
    assert task.progress_updates.count() == 1
    draft.refresh_from_db()
    assert draft.confirmed_at is not None


@pytest.mark.django_db
def test_invalid_item_rolls_back_entire_confirmation(draft):
    with pytest.raises(DraftConfirmationError):
        confirm_draft(draft.id, {"tasks": [{"action": "create", "project_id": 99999}]})
    assert Task.objects.count() == 0
    draft.refresh_from_db()
    assert draft.confirmed_at is None


@pytest.mark.django_db
def test_confirmed_draft_cannot_be_confirmed_twice(draft):
    project = Project.objects.create(name="SKU改造")
    decisions = {"tasks": [{"action": "create", "project_id": project.id}]}
    confirm_draft(draft.id, decisions)
    with pytest.raises(DraftAlreadyConfirmed):
        confirm_draft(draft.id, decisions)
