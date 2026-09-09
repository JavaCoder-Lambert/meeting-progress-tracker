from datetime import timedelta

import pytest
from django.utils import timezone

from core.models import ImportDraft, MeetingNote, ProgressUpdate, Project, ProjectPhase, Task


@pytest.fixture
def planning_draft(db):
    project = Project.objects.create(name="仓配改造")
    phase = ProjectPhase.objects.create(project=project, name="联调")
    task = Task.objects.create(project=project, phase=phase, title="接口联调", planned_for=timezone.localdate())
    note = MeetingNote.objects.create(title="进度确认", meeting_date=timezone.localdate(), raw_text="联调50%", parse_status="success")
    draft = ImportDraft.objects.create(meeting_note=note, payload={
        "tasks": [{"title": task.title, "project_name": project.name, "progress": 50, "status": "in_progress"}],
    })
    return draft, task, phase


def form(client, draft, task, **changes):
    page = client.get(f"/drafts/{draft.pk}/")
    return {"review_baseline": page.context["review_baseline"], "review_version": page.context["draft"].review_version,
            "task_0_action": "update", "task_0_existing": task.pk, "task_0_project": task.project_id,
            **changes}


def test_review_shows_existing_plan_and_preserves_it_when_untouched(admin_client, planning_draft):
    draft, task, phase = planning_draft
    row = admin_client.get(f"/drafts/{draft.pk}/").context["task_rows"][0]
    assert row["planning"] == {"phase_id": str(phase.pk), "planned_for": timezone.localdate().isoformat()}
    response = admin_client.post(f"/drafts/{draft.pk}/confirm/", form(admin_client, draft, task,
        task_0_phase=str(phase.pk), task_0_planned_for=timezone.localdate().isoformat()))
    assert response.status_code == 302
    task.refresh_from_db()
    assert task.phase_id == phase.pk
    assert task.planned_for == timezone.localdate()


def test_review_stashes_and_imports_explicit_plan_changes(admin_client, planning_draft):
    draft, task, _ = planning_draft
    phase = ProjectPhase.objects.create(project=task.project, name="验收")
    scheduled = (timezone.localdate() + timedelta(days=2)).isoformat()
    data = form(admin_client, draft, task, task_0_phase=str(phase.pk), task_0_planned_for=scheduled)
    assert admin_client.post(f"/drafts/{draft.pk}/save/", data).status_code == 302
    row = admin_client.get(f"/drafts/{draft.pk}/").context["task_rows"][0]
    assert row["planning"] == {"phase_id": str(phase.pk), "planned_for": scheduled}
    task.refresh_from_db()
    assert task.phase_id != phase.pk
    assert admin_client.post(f"/drafts/{draft.pk}/confirm/", form(admin_client, draft, task,
        task_0_phase=str(phase.pk), task_0_planned_for=scheduled)).status_code == 302
    task.refresh_from_db()
    assert task.phase_id == phase.pk
    assert task.planned_for.isoformat() == scheduled
    assert ProgressUpdate.objects.get().snapshot["planned_for"] == scheduled


def test_explicitly_clear_existing_plan(admin_client, planning_draft):
    draft, task, _ = planning_draft
    response = admin_client.post(f"/drafts/{draft.pk}/confirm/", form(admin_client, draft, task,
        task_0_phase="", task_0_planned_for=""))
    assert response.status_code == 302
    task.refresh_from_db()
    assert task.phase_id is None
    assert task.planned_for is None


def test_other_project_phase_rejected_without_changing_task(admin_client, planning_draft):
    draft, task, _ = planning_draft
    other = Project.objects.create(name="财务")
    phase = ProjectPhase.objects.create(project=other, name="财务联调")
    response = admin_client.post(f"/drafts/{draft.pk}/confirm/", form(admin_client, draft, task, task_0_phase=str(phase.pk)))
    assert response.status_code == 200
    assert "阶段" in response.context["error"]
    assert not ProgressUpdate.objects.exists()


def test_historical_plan_changes_only_affect_snapshot(admin_client, planning_draft):
    draft, task, phase = planning_draft
    draft.meeting_note.meeting_date -= timedelta(days=3)
    draft.meeting_note.save()
    response = admin_client.post(f"/drafts/{draft.pk}/confirm/", form(admin_client, draft, task,
        task_0_phase="", task_0_planned_for=""))
    assert response.status_code == 302
    task.refresh_from_db()
    assert task.phase_id == phase.pk
    assert task.planned_for == timezone.localdate()
    assert ProgressUpdate.objects.get().snapshot["planned_for"] == ""


@pytest.mark.parametrize("historical", [False, True])
def test_clear_after_ignored_row_autofills_target_is_explicit(admin_client, planning_draft, historical):
    draft, task, phase = planning_draft
    if historical:
        draft.meeting_note.meeting_date -= timedelta(days=3)
        draft.meeting_note.save()
    draft.review_state = {"payload": draft.payload, "decisions": {"tasks": [{"action": "ignore", "task_id": None,
        "project_id": str(task.project_id), "planning": {}, "planning_provided": []}], "risks": [], "milestones": []}}
    draft.save()
    page = admin_client.get(f"/drafts/{draft.pk}/")
    assert page.context["task_rows"][0]["planning"] == {"phase_id": "", "planned_for": ""}
    data = form(admin_client, draft, task, task_0_phase="", task_0_planned_for="",
        task_0_phase_edited="true", task_0_planned_for_edited="true")
    assert admin_client.post(f"/drafts/{draft.pk}/save/", data).status_code == 302
    draft.refresh_from_db()
    assert draft.review_state["decisions"]["tasks"][0]["planning_provided"] == ["phase_id", "planned_for"]
    # Existing autosave protocol retains the original page baseline, advancing only version.
    data["review_version"] = draft.review_version
    assert admin_client.post(f"/drafts/{draft.pk}/confirm/", data).status_code == 302
    task.refresh_from_db()
    assert task.phase_id == (phase.pk if historical else None)
    assert task.planned_for == (timezone.localdate() if historical else None)
    update = ProgressUpdate.objects.get()
    assert update.snapshot["planned_for"] == ""


def test_autofilled_target_switch_is_not_an_explicit_planning_edit(admin_client, planning_draft):
    from core.services.ai_history import task_baseline
    draft, task, _ = planning_draft
    second = Task.objects.create(project=task.project, title="接口联调二期")
    draft.review_state = {"payload": draft.payload, "decisions": {"tasks": [{"action": "update", "task_id": str(task.pk),
        "project_id": str(task.project_id), "task_baseline": task_baseline(task, draft), "planning": {}, "planning_provided": []}], "risks": [], "milestones": []}}
    draft.save()
    data = form(admin_client, draft, task, task_0_existing=second.pk, task_0_phase="", task_0_planned_for="",
        task_0_phase_edited="false", task_0_planned_for_edited="false")
    assert admin_client.post(f"/drafts/{draft.pk}/save/", data).status_code == 302
    draft.refresh_from_db()
    assert draft.review_state["decisions"]["tasks"][0]["planning_provided"] == []
