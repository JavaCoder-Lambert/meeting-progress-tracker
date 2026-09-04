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


@pytest.mark.django_db
def test_update_requires_an_existing_task_selection(draft):
    project = Project.objects.create(name="SKU改造")

    with pytest.raises(DraftConfirmationError, match="必须选择已有任务"):
        confirm_draft(
            draft.id,
            {"tasks": [{"action": "update", "project_id": project.id, "task_id": None}]},
        )

    assert Task.objects.count() == 0


@pytest.mark.django_db
def test_only_latest_draft_for_a_meeting_can_be_confirmed(draft):
    project = Project.objects.create(name="SKU改造")
    ImportDraft.objects.create(meeting_note=draft.meeting_note, payload=draft.payload)

    with pytest.raises(DraftConfirmationError, match="较新的解析结果"):
        confirm_draft(draft.id, {"tasks": [{"action": "create", "project_id": project.id}]})

    assert Task.objects.count() == 0


@pytest.mark.django_db
def test_a_meeting_cannot_import_more_than_one_draft(draft):
    project = Project.objects.create(name="SKU改造")
    decisions = {"tasks": [{"action": "create", "project_id": project.id}]}
    confirm_draft(draft.id, decisions)
    newer = ImportDraft.objects.create(meeting_note=draft.meeting_note, payload=draft.payload)

    with pytest.raises(DraftAlreadyConfirmed, match="已经入库"):
        confirm_draft(newer.id, decisions)

    assert Task.objects.count() == 1


@pytest.mark.django_db
def test_update_does_not_clear_existing_optional_details_with_empty_ai_values(draft):
    project = Project.objects.create(name="SKU改造")
    existing = Task.objects.create(
        project=project,
        title="发货仓库优先级逻辑调整",
        description="保留的任务背景",
        planned_start_date=date(2026, 8, 31),
        due_date=date(2026, 9, 20),
        current_note="上一轮说明",
    )
    payload = {**draft.payload, "tasks": [{
        **draft.payload["tasks"][0],
        "description": "",
        "planned_start_date": None,
        "due_date": None,
        "acceptance_date": None,
        "current_note": "",
    }]}

    confirm_draft(
        draft.id,
        {"tasks": [{"action": "update", "project_id": project.id, "task_id": existing.id}]},
        payload=payload,
    )

    existing.refresh_from_db()
    assert existing.description == "保留的任务背景"
    assert existing.planned_start_date == date(2026, 8, 31)
    assert existing.due_date == date(2026, 9, 20)
    assert existing.current_note == "上一轮说明"
