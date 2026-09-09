from datetime import timedelta

import pytest
from django.utils import timezone

from core.models import ImportDraft, MeetingNote, Person, ProgressUpdate, Project, ProjectPhase, Risk, Task
from core.services.ai_history import task_baseline
from core.services.draft_confirmation import DraftConfirmationError, confirm_draft
from core.services.reports import build_weekly_report


def make_draft(project, offset=0, **values):
    note = MeetingNote.objects.create(
        title="进度会", meeting_date=timezone.localdate() + timedelta(days=offset),
        raw_text="接口联调进展", parse_status="success",
    )
    return ImportDraft.objects.create(meeting_note=note, payload={
        "tasks": [{"title": "接口联调", "project_name": project.name,
                   "status": "in_progress", "progress": 40,
                   "completed_work": "历史联调记录", "next_step": "继续验收", **values}],
    })


def review_submission(client, draft, task):
    page = client.get(f"/drafts/{draft.pk}/")
    return {"review_baseline": page.context["review_baseline"],
            "review_version": page.context["draft"].review_version,
            "task_0_action": "update", "task_0_existing": task.pk,
            "task_0_project": task.project_id, "task_0_title": "接口联调",
            "task_0_status": "in_progress", "task_0_progress": 40,
            "task_0_next_step": "保留审阅修改"}


@pytest.mark.django_db
def test_historical_ai_update_records_snapshot_without_overwriting_current_task():
    project = Project.objects.create(name="仓配")
    person = Person.objects.create(name="张川")
    completed_at = timezone.now()
    task = Task.objects.create(project=project, assignee=person, title="接口已上线",
                               status="done", progress=100, current_note="已完成验收",
                               completed_at=completed_at)
    original_updated_at = task.updated_at
    draft = make_draft(project, offset=-3)

    confirm_draft(draft.pk, {"tasks": [{"action": "update", "task_id": task.pk,
                                      "project_id": project.pk, "assignee_id": person.pk}]})

    task.refresh_from_db()
    assert (task.title, task.status, task.progress, task.current_note) == (
        "接口已上线", "done", 100, "已完成验收")
    assert task.completed_at == completed_at
    assert task.updated_at == original_updated_at
    update = ProgressUpdate.objects.get()
    assert update.occurred_on == draft.meeting_note.meeting_date
    assert update.applied_to_task is False
    assert (update.new_status, update.new_progress) == ("in_progress", 40)
    assert update.snapshot["title"] == "接口联调"
    assert update.snapshot["project_name"] == "仓配"
    assert update.snapshot["person_name"] == "张川"
    assert update.snapshot["project_id"] == project.pk
    assert update.snapshot["person_id"] == person.pk
    task.title = "后续任务名"
    task.save()
    project.name = "后续项目名"
    project.save()
    report = build_weekly_report(draft.meeting_note.meeting_date, draft.meeting_note.meeting_date).markdown
    assert "## 仓配" in report
    assert "接口联调：历史联调记录" in report
    assert "后续任务名" not in report
    today_report = build_weekly_report(timezone.localdate(), timezone.localdate()).markdown
    assert "历史联调记录" not in today_report


@pytest.mark.django_db
def test_historical_ai_created_done_task_uses_business_completion_date():
    project = Project.objects.create(name="仓配")
    draft = make_draft(project, offset=-3, status="done", progress=100)
    confirm_draft(draft.pk, {"tasks": [{"action": "create", "project_id": project.pk}]})
    task = Task.objects.get()
    assert timezone.localdate(task.completed_at) == draft.meeting_note.meeting_date
    update = ProgressUpdate.objects.get()
    assert update.occurred_on == draft.meeting_note.meeting_date
    assert update.applied_to_task is True
    assert update.snapshot["status"] == "done"


@pytest.mark.django_db
def test_future_ai_meeting_cannot_be_confirmed():
    project = Project.objects.create(name="仓配")
    draft = make_draft(project, offset=1)
    with pytest.raises(DraftConfirmationError, match="未来"):
        confirm_draft(draft.pk, {"tasks": [{"action": "create", "project_id": project.pk}]})
    assert not Task.objects.exists()
    assert not ProgressUpdate.objects.exists()
    draft.refresh_from_db()
    assert draft.confirmed_at is None
    assert draft.meeting_note.parse_status == "success"


@pytest.mark.django_db
def test_same_day_changed_task_requires_explicit_refresh_and_another_review(admin_client):
    project = Project.objects.create(name="仓配")
    task = Task.objects.create(project=project, title="接口联调", status="in_progress", progress=20)
    draft = make_draft(project)
    data = review_submission(admin_client, draft, task)
    saved = admin_client.post(f"/drafts/{draft.pk}/save/", data)
    assert saved.status_code == 302
    task.progress = 80
    task.save()
    data = review_submission(admin_client, draft, task)

    conflict = admin_client.post(f"/drafts/{draft.pk}/confirm/", data)

    assert conflict.status_code == 409
    task.refresh_from_db()
    assert task.progress == 80
    assert not ProgressUpdate.objects.exists()
    page = admin_client.get(f"/drafts/{draft.pk}/")
    assert page.context["task_rows"][0]["item"]["next_step"] == "保留审阅修改"
    assert b'value="refresh_tasks"' in page.content
    data = review_submission(admin_client, draft, task)
    refreshed = admin_client.post(f"/drafts/{draft.pk}/save/", {**data, "destination": "refresh_tasks"})
    assert refreshed.status_code == 302
    assert not ProgressUpdate.objects.exists()
    page = admin_client.get(refreshed.url)
    progress_diff = next(row for row in page.context["task_rows"][0]["diffs"] if row["field"] == "progress")
    assert (progress_diff["existing"], progress_diff["incoming"]) == (80, 40)
    data = review_submission(admin_client, draft, task)
    assert admin_client.post(f"/drafts/{draft.pk}/confirm/", data).status_code == 302
    task.refresh_from_db()
    assert task.progress == 40
    update = ProgressUpdate.objects.get()
    assert (update.previous_progress, update.new_progress) == (80, 40)
    assert update.occurred_on == timezone.localdate()
    assert update.applied_to_task is True


@pytest.mark.django_db
def test_same_day_confirmation_requires_reviewed_task_baseline(admin_client):
    project = Project.objects.create(name="仓配")
    task = Task.objects.create(project=project, title="接口联调", progress=80)
    draft = make_draft(project)
    data = review_submission(admin_client, draft, task)
    data.pop("review_baseline")
    response = admin_client.post(f"/drafts/{draft.pk}/confirm/", data)
    assert response.status_code == 409
    task.refresh_from_db()
    assert task.progress == 80
    assert not ProgressUpdate.objects.exists()


@pytest.mark.django_db
def test_same_day_service_rejects_an_unreviewed_update():
    project = Project.objects.create(name="仓配")
    task = Task.objects.create(project=project, title="接口联调", progress=80)
    draft = make_draft(project)
    with pytest.raises(DraftConfirmationError, match="基线"):
        confirm_draft(draft.pk, {"tasks": [{"action": "update", "task_id": task.pk,
                                          "project_id": project.pk}]})
    task.refresh_from_db()
    assert task.progress == 80


@pytest.mark.django_db
def test_legacy_saved_update_requires_explicit_review_before_current_task_changes(admin_client):
    project = Project.objects.create(name="仓配")
    task = Task.objects.create(project=project, title="接口联调", progress=80)
    draft = make_draft(project)
    draft.review_state = {"payload": draft.payload, "decisions": {"tasks": [
        {"action": "update", "project_id": str(project.pk), "task_id": str(task.pk)}]}}
    draft.save(update_fields=["review_state"])
    data = review_submission(admin_client, draft, task)
    assert admin_client.post(f"/drafts/{draft.pk}/confirm/", data).status_code == 409
    task.refresh_from_db()
    assert task.progress == 80


@pytest.mark.django_db
def test_historical_omissions_retain_reviewed_state_when_task_changes_after_review(admin_client):
    project = Project.objects.create(name="仓配")
    task = Task.objects.create(project=project, title="接口联调", status="in_progress", progress=20)
    draft = make_draft(project, offset=-2)
    draft.payload["tasks"][0].pop("status")
    draft.payload["tasks"][0].pop("progress")
    draft.save(update_fields=["payload"])
    data = review_submission(admin_client, draft, task)
    data.pop("task_0_status")
    data.pop("task_0_progress")
    task.progress = 90
    task.status = "acceptance"
    task.save()
    assert admin_client.post(f"/drafts/{draft.pk}/confirm/", data).status_code == 302
    update = ProgressUpdate.objects.get()
    assert (update.previous_progress, update.new_progress) == (20, 20)
    assert (update.previous_status, update.new_status) == ("in_progress", "in_progress")
    task.refresh_from_db()
    assert (task.status, task.progress) == ("acceptance", 90)


@pytest.mark.django_db
def test_refresh_displays_current_values_for_omitted_ai_fields_and_keeps_explicit_edits(admin_client):
    project = Project.objects.create(name="仓配")
    task = Task.objects.create(project=project, title="接口联调", status="in_progress", progress=20)
    draft = make_draft(project)
    draft.payload["tasks"][0].pop("status")
    draft.payload["tasks"][0].pop("progress")
    draft.save(update_fields=["payload"])
    data = review_submission(admin_client, draft, task)
    data.update(task_0_progress=20)
    task.progress = 90
    task.status = "acceptance"
    task.save()
    refreshed = admin_client.post(f"/drafts/{draft.pk}/save/", {**data, "destination": "refresh_tasks"})
    assert refreshed.status_code == 302
    page = admin_client.get(refreshed.url)
    item = page.context["task_rows"][0]["item"]
    assert (item["status"], item["progress"]) == ("acceptance", 90)
    assert item["next_step"] == "保留审阅修改"


@pytest.mark.django_db
@pytest.mark.parametrize("invalid_kind", ["tampered", "other_draft", "other_task"])
def test_task_baseline_cannot_be_forged_or_reused_for_another_target(invalid_kind):
    project = Project.objects.create(name="仓配")
    task = Task.objects.create(project=project, title="接口联调", progress=80)
    draft = make_draft(project)
    token = task_baseline(task, draft)
    if invalid_kind == "tampered":
        token += "tampered"
    elif invalid_kind == "other_draft":
        token = task_baseline(task, make_draft(project))
    else:
        other = Task.objects.create(project=project, title="另一项任务")
        token = task_baseline(other, draft)
    with pytest.raises(DraftConfirmationError, match="基线"):
        confirm_draft(draft.pk, {"tasks": [{"action": "update", "task_id": task.pk,
            "project_id": project.pk, "task_baseline": token}]})
    task.refresh_from_db()
    assert task.progress == 80
    assert not ProgressUpdate.objects.exists()


@pytest.mark.django_db
def test_task_change_after_review_rolls_back_earlier_rows_even_without_timestamp_change():
    project = Project.objects.create(name="仓配")
    task = Task.objects.create(project=project, title="接口联调", progress=80)
    draft = make_draft(project)
    token = task_baseline(task, draft)
    draft.payload["tasks"].insert(0, {"title": "本次新任务"})
    draft.save(update_fields=["payload"])
    Task.objects.filter(pk=task.pk).update(title="任务中心的新标题")
    with pytest.raises(DraftConfirmationError, match="已被修改"):
        confirm_draft(draft.pk, {"tasks": [
            {"action": "create", "project_id": project.pk},
            {"action": "update", "task_id": task.pk, "project_id": project.pk, "task_baseline": token},
        ]})
    assert Task.objects.count() == 1
    assert not ProgressUpdate.objects.exists()
    draft.refresh_from_db()
    assert draft.confirmed_at is None


@pytest.mark.django_db
def test_confirmed_review_does_not_reclassify_applied_updates_as_historical_next_day(admin_client, monkeypatch):
    project = Project.objects.create(name="仓配")
    task = Task.objects.create(project=project, title="接口联调", progress=20)
    draft = make_draft(project)
    data = review_submission(admin_client, draft, task)
    assert admin_client.post(f"/drafts/{draft.pk}/confirm/", data).status_code == 302
    tomorrow = timezone.localdate() + timedelta(days=1)
    actual_localdate = timezone.localdate
    monkeypatch.setattr(timezone, "localdate", lambda value=None, timezone=None:
                        actual_localdate(value, timezone) if value is not None else tomorrow)
    page = admin_client.get(f"/drafts/{draft.pk}/")
    assert "已有任务仅补记历史进展" not in page.content.decode()
    assert ProgressUpdate.objects.get().applied_to_task is True


@pytest.mark.django_db
@pytest.mark.parametrize("offset", [0, -2])
def test_duplicate_update_targets_explain_how_to_resolve_without_business_writes(admin_client, offset):
    project = Project.objects.create(name="仓配")
    task = Task.objects.create(project=project, title="接口联调", progress=20)
    draft = make_draft(project, offset=offset)
    draft.payload["tasks"].append(dict(draft.payload["tasks"][0]))
    draft.save(update_fields=["payload"])
    data = review_submission(admin_client, draft, task)
    data.update({name.replace("task_0_", "task_1_"): value for name, value in data.copy().items()
                 if name.startswith("task_0_")})
    response = admin_client.post(f"/drafts/{draft.pk}/confirm/", data)
    assert response.status_code == 200
    assert "同一任务不能重复更新，请合并记录或忽略重复项" in response.content.decode()
    task.refresh_from_db()
    assert task.progress == 20
    assert not ProgressUpdate.objects.exists()
    draft.refresh_from_db()
    assert draft.confirmed_at is None
    assert draft.meeting_note.parse_status == "success"


@pytest.mark.django_db
@pytest.mark.parametrize("person_change", ["reassigned", "renamed", "deleted", "explicit_selection"])
def test_historical_person_uses_reviewed_identity_unless_another_person_is_selected(admin_client, person_change):
    project = Project.objects.create(name="仓配")
    reviewed_person = Person.objects.create(name="原负责人")
    reviewed_person_id = reviewed_person.pk
    current_person = Person.objects.create(name="当前负责人")
    selected_person = Person.objects.create(name="手动选择负责人")
    task = Task.objects.create(project=project, assignee=reviewed_person, title="接口联调", progress=10)
    draft = make_draft(project, offset=-2)
    data = review_submission(admin_client, draft, task)
    task.assignee = current_person
    task.progress = 90
    task.save()
    if person_change == "renamed":
        reviewed_person.name = "后来改名"
        reviewed_person.save()
    elif person_change == "deleted":
        reviewed_person.delete()
    elif person_change == "explicit_selection":
        data["task_0_assignee"] = selected_person.pk
    response = admin_client.post(f"/drafts/{draft.pk}/confirm/", data)
    assert response.status_code == 302
    update = ProgressUpdate.objects.get()
    expected = (selected_person.pk, "手动选择负责人") if person_change == "explicit_selection" else (
        reviewed_person_id, "原负责人")
    assert (update.snapshot["person_id"], update.snapshot["person_name"]) == expected
    task.refresh_from_db()
    assert (task.assignee_id, task.progress) == (current_person.pk, 90)


@pytest.mark.django_db
@pytest.mark.parametrize("live_link", ["phase", "risk"])
def test_historical_snapshot_can_keep_original_project_after_task_moves(admin_client, live_link):
    project = Project.objects.create(name="会议时项目")
    current_project = Project.objects.create(name="当前项目")
    task = Task.objects.create(project=project, title="接口联调", progress=10)
    draft = make_draft(project, offset=-2)
    data = review_submission(admin_client, draft, task)
    task.project = current_project
    task.progress = 90
    if live_link == "phase":
        task.phase = ProjectPhase.objects.create(project=current_project, name="当前阶段")
    else:
        Risk.objects.create(project=current_project, task=task, content="当前风险")
    task.save()
    response = admin_client.post(f"/drafts/{draft.pk}/confirm/", data)
    assert response.status_code == 302
    update = ProgressUpdate.objects.get()
    assert (update.snapshot["project_id"], update.snapshot["project_name"]) == (project.pk, "会议时项目")
    assert update.applied_to_task is False
    task.refresh_from_db()
    assert (task.project_id, task.progress) == (current_project.pk, 90)
    if live_link == "phase":
        assert task.phase.project_id == current_project.pk
    else:
        assert task.risks.get().project_id == current_project.pk


@pytest.mark.django_db
def test_new_risk_still_uses_live_task_project_during_historical_confirmation(admin_client):
    project = Project.objects.create(name="会议时项目")
    current_project = Project.objects.create(name="当前项目")
    task = Task.objects.create(project=project, title="接口联调", progress=10)
    draft = make_draft(project, offset=-2)
    draft.payload["risks"] = [{"content": "历史风险", "task_title": "接口联调"}]
    draft.save(update_fields=["payload"])
    data = review_submission(admin_client, draft, task)
    data.update(risks_0_action="create", risks_0_project=project.pk)
    task.project = current_project
    task.save()
    response = admin_client.post(f"/drafts/{draft.pk}/confirm/", data)
    assert response.status_code == 200
    assert "风险关联任务必须属于同一项目" in response.content.decode()
    assert not Risk.objects.exists()
    assert not ProgressUpdate.objects.exists()
    task.refresh_from_db()
    assert task.project_id == current_project.pk
