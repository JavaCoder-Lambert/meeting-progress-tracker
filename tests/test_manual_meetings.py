from copy import deepcopy
from datetime import timedelta
from uuid import uuid4

import pytest
from django.utils import timezone

from core.models import MeetingNote, Person, ProgressUpdate, Project, Risk, Task
from core.services.manual_meetings import (
    ManualMeetingError, confirm_session, create_session, preview_session,
    save_session, serialize_session, task_option,
)

pytestmark = pytest.mark.django_db


def state(**kwargs):
    return {"title": "周会", "meeting_date": timezone.localdate().isoformat(), **kwargs}


@pytest.fixture
def task():
    return Task.objects.create(project=Project.objects.create(name="项目"),
                               assignee=Person.objects.create(name="负责人"), title="已有任务", progress=10)


def item(task=None, **kwargs):
    return {"id": str(uuid4()), "kind": "task" if task else "note", "recorded": True,
            **({"task_id": task.pk, "baseline": task_option(task)["baseline"]} if task else {}), **kwargs}


def test_draft_survives_resume_without_formal_writes(task):
    draft = create_session(state(items=[item(task, completed_work="讨论中")]))
    saved = save_session(draft.pk, 0, {**draft.state, "agenda": "议题"})
    saved.refresh_from_db()
    assert serialize_session(saved)["state"]["agenda"] == "议题"
    assert saved.state["items"][0]["id"] == draft.state["items"][0]["id"]
    assert saved.version == 1
    assert not ProgressUpdate.objects.exists()
    task.refresh_from_db()
    assert task.progress == 10


def test_confirm_once_keeps_reporter_separate_and_unrecorded_task_untouched(task):
    reporter = Person.objects.create(name="汇报人")
    untouched = Task.objects.create(project=task.project, title="未询问")
    draft = create_session(state(meeting_time="09:30", items=[
        item(task, person_id=reporter.pk, status="done", progress=50, completed_work="交付", next_step="观察"),
        item(untouched, recorded=False, status="done"),
    ]))
    confirmed = confirm_session(draft.pk, draft.version)
    assert confirmed.confirmed_at and confirmed.minutes
    assert confirm_session(draft.pk, draft.version).pk == confirmed.pk
    task.refresh_from_db()
    untouched.refresh_from_db()
    assert task.progress == 100 and task.status == "done"
    assert task.assignee.name == "负责人"
    assert timezone.localtime(task.completed_at).strftime("%H:%M") == "09:30"
    assert untouched.status == "not_started"
    update = ProgressUpdate.objects.get()
    assert update.snapshot["person_name"] == "汇报人"
    assert update.occurred_on == timezone.localdate()
    assert update.meeting_note_id == draft.meeting_note_id


def test_stale_current_task_rejected_but_history_uses_signed_original(task):
    card = item(task, progress=20)
    current = create_session(state(items=[card]))
    past = create_session(state(meeting_date=(timezone.localdate() - timedelta(days=7)).isoformat(), items=[card]))
    task.title = "现在的名称"
    task.progress = 90
    task.save()
    with pytest.raises(ManualMeetingError) as error:
        confirm_session(current.pk, 0)
    assert error.value.conflict
    current.refresh_from_db()
    assert current.version == 0
    confirm_session(past.pk, 0)
    task.refresh_from_db()
    assert task.progress == 90 and task.title == "现在的名称"
    update = ProgressUpdate.objects.get()
    assert update.previous_progress == 10 and update.new_progress == 20
    assert update.snapshot["title"] == "已有任务"
    assert not update.applied_to_task


def test_incomplete_draft_saves_and_new_records_confirm_by_id(task):
    cards = [item(kind="new_task", title="新任务", status="in_progress", progress=25),
             item(kind="risk", content="依赖延期", task_id=task.pk),
             item(kind="decision", content="选方案"), item(content="普通纪要")]
    draft = create_session(state(items=cards))
    assert len(preview_session(draft)["errors"]) == 3
    with pytest.raises(ManualMeetingError):
        confirm_session(draft.pk, 0)
    for card in cards[:3]:
        card.update(project_id=task.project_id, person_id=task.assignee_id)
    draft = save_session(draft.pk, 0, state(items=cards))
    preview = preview_session(draft)
    assert preview["counts"] == {"tasks": 0, "new_tasks": 1, "risks": 2, "notes": 1}
    confirmed = confirm_session(draft.pk, 1)
    created = Task.objects.get(title="新任务")
    assert created.project_id == task.project_id and created.assignee_id == task.assignee_id
    assert created.source_meeting_id == draft.meeting_note_id
    assert Risk.objects.get(risk_type="risk").task_id == task.pk
    assert Risk.objects.get(risk_type="decision").owner_id == task.assignee_id
    assert "普通纪要" in confirmed.minutes
    confirmed.meeting_note.refresh_from_db()
    assert confirmed.meeting_note.raw_text == confirmed.minutes


def test_preview_and_minutes_include_deadlines_planning_and_new_task_description(task):
    due = (timezone.localdate() + timedelta(days=4)).isoformat()
    planned = (timezone.localdate() + timedelta(days=2)).isoformat()
    draft = create_session(state(items=[item(task, due_date=due, planned_for=planned),
        item(kind="new_task", title="详细新任务", project_id=task.project_id, content="不能遗漏的任务说明")]))
    preview = preview_session(draft)
    assert due in preview["items"][0]["summary"]
    assert planned in preview["minutes"]
    assert "不能遗漏的任务说明" in preview["minutes"]


@pytest.mark.parametrize("changes", [
    {"items": "bad"}, {"title": ["bad"]}, {"meeting_date": "yesterday"},
    {"meeting_time": "99:00"}, {"project_ids": [True]}, {"items": [{}]},
    {"items": [item(progress=True)]}, {"items": [item(progress=101)]},
    {"items": [item(kind="unknown")]}, {"items": [item(recorded="false")]},
    {"items": [item(content="x" * 20001)]}, {"items": [item() for _ in range(301)]},
    {"person_ids": [99999]}, {"items": [item(person_id=99999)]},
])
def test_malformed_drafts_fail_readably_without_writes(changes):
    with pytest.raises(ManualMeetingError):
        create_session(state(**changes))
    assert not MeetingNote.objects.exists()


@pytest.mark.parametrize("case", ["future", "wrong_baseline", "broken_baseline", "missing_baseline", "missing_task", "invalid_status", "invalid_dates", "deleted_task"])
def test_preview_rejects_unsafe_confirmation_without_writes(task, case):
    card = item(task, status="done")
    data = state(items=[card])
    if case == "future":
        data["meeting_date"] = (timezone.localdate() + timedelta(days=1)).isoformat()
    elif case == "wrong_baseline":
        other = Task.objects.create(project=task.project, title="另一个")
        card["baseline"] = task_option(other)["baseline"]
    elif case == "broken_baseline":
        card["baseline"] += "x"
    elif case == "missing_baseline":
        card["baseline"] = ""
    elif case == "missing_task":
        card["task_id"] = None
    elif case == "invalid_status":
        card["status"] = "made_up"
    elif case == "invalid_dates":
        task.planned_start_date = timezone.localdate()
        task.save()
        card["baseline"] = task_option(task)["baseline"]
        card["due_date"] = (timezone.localdate() - timedelta(days=1)).isoformat()
    draft = create_session(data)
    if case == "deleted_task":
        task.delete()
    assert preview_session(draft)["errors"]
    with pytest.raises(ManualMeetingError):
        confirm_session(draft.pk, 0)
    assert not ProgressUpdate.objects.exists()
    draft.refresh_from_db()
    assert draft.confirmed_at is None and draft.version == 0


def test_version_conflicts_and_duplicate_cards_preserve_saved_state(task):
    draft = create_session(state())
    save_session(draft.pk, 0, state(agenda="已保存"))
    for operation in (lambda: save_session(draft.pk, 0, state(agenda="过期")), lambda: confirm_session(draft.pk, 0)):
        with pytest.raises(ManualMeetingError) as error:
            operation()
        assert error.value.conflict
    with pytest.raises(ManualMeetingError):
        save_session(draft.pk, 1, state(items=[item(task), item(task)]))
    same = item()
    with pytest.raises(ManualMeetingError):
        save_session(draft.pk, 1, state(items=[same, same]))
    draft.refresh_from_db()
    assert draft.version == 1 and draft.state["agenda"] == "已保存"
    confirm_session(draft.pk, 1)
    with pytest.raises(ManualMeetingError):
        save_session(draft.pk, 2, state(agenda="不能改"))


def test_confirmation_rolls_back_all_effects_on_late_database_failure(task, monkeypatch):
    draft = create_session(state(items=[item(task, progress=80), item(kind="risk", project_id=task.project_id, content="风险")]))
    def fail_save(*args, **kwargs):
        raise RuntimeError("simulated storage failure")
    monkeypatch.setattr(Risk, "save", fail_save)
    with pytest.raises(RuntimeError, match="simulated storage failure"):
        confirm_session(draft.pk, 0)
    task.refresh_from_db()
    draft.refresh_from_db()
    assert task.progress == 10
    assert not ProgressUpdate.objects.exists()
    assert draft.version == 0 and draft.confirmed_at is None
    draft.meeting_note.refresh_from_db()
    assert draft.meeting_note.raw_text == ""


@pytest.mark.parametrize("historical", [False, True])
def test_existing_task_supplement_is_retained_in_history_minutes_and_current_note(task, historical):
    meeting_date = timezone.localdate() - timedelta(days=int(historical))
    draft = create_session(state(meeting_date=meeting_date.isoformat(), items=[item(task, content="补充说明\n第二行")]))
    preview = preview_session(draft)
    assert "补充说明\n第二行" in preview["items"][0]["summary"]
    assert "；；" not in preview["items"][0]["summary"]
    confirmed = confirm_session(draft.pk, 0)
    task.refresh_from_db()
    assert task.current_note == ("" if historical else "补充说明\n第二行")
    update = ProgressUpdate.objects.get()
    assert update.snapshot["content"] == "补充说明\n第二行"
    assert "补充说明\n第二行" in confirmed.minutes
    assert update.applied_to_task is not historical
    confirmed.meeting_note.refresh_from_db()
    assert "补充说明\n第二行" in confirmed.meeting_note.raw_text


def test_deleted_metadata_reference_cannot_confirm(task):
    person = Person.objects.create(name="参与人")
    draft = create_session(state(person_ids=[person.pk], items=[item(task, progress=80)]))
    person.delete()
    assert preview_session(draft)["errors"]
    with pytest.raises(ManualMeetingError):
        confirm_session(draft.pk, 0)


def test_new_task_records_business_dated_progress_and_snapshot(task):
    yesterday = timezone.localdate() - timedelta(days=1)
    draft = create_session(state(meeting_date=yesterday.isoformat(), items=[item(kind="new_task", title="补录新任务",
        project_id=task.project_id, person_id=task.assignee_id, progress=40, completed_work="已联调", next_step="发布")]))
    confirm_session(draft.pk, 0)
    update = ProgressUpdate.objects.get()
    assert update.task.title == "补录新任务" and update.occurred_on == yesterday
    assert update.new_progress == 40 and update.completed_work == "已联调" and update.next_step == "发布"
    assert update.snapshot["project_name"] == task.project.name and update.snapshot["person_name"] == task.assignee.name
    assert update.applied_to_task


def test_preview_shows_signed_before_and_after_values(task):
    old_due = timezone.localdate().isoformat()
    new_due = (timezone.localdate() + timedelta(days=3)).isoformat()
    task.due_date = timezone.localdate()
    task.save()
    draft = create_session(state(items=[item(task, status="done", progress=90, due_date=new_due, planned_for=new_due)]))
    summary = preview_session(draft)["items"][0]["summary"]
    assert "未开始 → 已完成" in summary and "10% → 100%" in summary
    assert f"{old_due} → {new_due}" in summary and f"未设置 → {new_due}" in summary


@pytest.mark.parametrize("version", ["²", "①", "٠", "０"])
@pytest.mark.parametrize("operation", ["save", "confirm"])
def test_non_ascii_version_is_a_readable_error(version, operation):
    draft = create_session(state())
    with pytest.raises(ManualMeetingError, match="版本无效"):
        if operation == "save":
            save_session(draft.pk, version, draft.state)
        else:
            confirm_session(draft.pk, version)
    draft.refresh_from_db()
    assert draft.version == 0 and draft.confirmed_at is None


def test_baseline_values_derive_from_signed_original_even_for_legacy_drafts(task):
    card = item(task, baseline_values={"progress": 99})
    draft = create_session(state(items=[card]))
    assert draft.state["items"][0]["baseline_values"]["progress"] == 10
    task.progress = 90
    task.save()
    draft.state["items"][0].pop("baseline_values")
    serialized = serialize_session(draft)
    values = serialized["state"]["items"][0]["baseline_values"]
    assert values == {"status": "not_started", "progress": 10, "title": "已有任务", "project_name": "项目",
                      "assignee_name": "负责人", "due_date": "", "planned_for": ""}
    assert "baseline_values" not in draft.state["items"][0]
    saved = save_session(draft.pk, 0, {**draft.state, "items": [{**draft.state["items"][0], "baseline_values": {"progress": 99}}]})
    assert saved.state["items"][0]["baseline_values"]["progress"] == 10
    saved.state["items"][0]["baseline"] += "invalid"
    assert serialize_session(saved)["state"]["items"][0]["baseline_values"] == {}


def test_new_task_summary_labels_nonempty_work_fields(task):
    draft = create_session(state(items=[item(kind="new_task", project_id=task.project_id, title="任务",
        completed_work="完成联调", next_step="等待发布", content="补充背景")]))
    summary = preview_session(draft)["items"][0]["summary"]
    assert "本次完成：完成联调\n下一步：等待发布\n补充说明：补充背景" in summary
    assert "；；" not in summary
