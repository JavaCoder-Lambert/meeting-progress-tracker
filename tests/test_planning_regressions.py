from datetime import date, timedelta

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from core.models import ImportDraft, MeetingNote, ProgressUpdate, Project, ProjectPhase, Risk, Task


pytestmark = pytest.mark.django_db


def edit_payload(client, task, **changes):
    data = {"project": task.project_id, "phase": task.phase_id or "", "title": task.title,
            "description": task.description, "assignee": task.assignee_id or "",
            "planned_for": task.planned_for or "", "planned_start_date": task.planned_start_date or "",
            "due_date": task.due_date or "", "acceptance_date": task.acceptance_date or "",
            "status": task.status, "priority": task.priority, "progress": task.progress,
            "current_note": task.current_note}
    baseline = client.get(f"/tasks/{task.pk}/edit/").context["form"]["task_baseline"].value()
    return {**data, "task_baseline": baseline, **changes}


@pytest.fixture
def task():
    project = Project.objects.create(name="订单履约")
    return Task.objects.create(project=project, title="接口联调", planned_start_date=date(2026, 9, 10), due_date=date(2026, 9, 16))


def test_progress_date_before_task_start_is_inline_validation_and_writes_nothing(admin_client, task):
    before = task.updated_at
    baseline = admin_client.get(f"/tasks/{task.pk}/").context["progress_form"]["task_baseline"].value()
    response = admin_client.post(f"/tasks/{task.pk}/progress/", {"due_date": "2026-09-09", "completed_work": "不应写入", "task_baseline": baseline})
    assert response.status_code == 200
    assert "截止日期不能早于计划开始" in response.content.decode()
    task.refresh_from_db()
    assert task.due_date == date(2026, 9, 16)
    assert task.updated_at == before
    assert not ProgressUpdate.objects.exists()


def test_progress_view_handles_service_validation_after_form_check(admin_client, task, monkeypatch):
    def validate_again(*args, **kwargs):
        raise ValidationError({"due_date": "计划开始已调整，请重新核对截止日期。", "phase": "阶段信息已更新。"})

    monkeypatch.setattr("core.views.record_task_progress", validate_again)
    baseline = admin_client.get(f"/tasks/{task.pk}/").context["progress_form"]["task_baseline"].value()
    response = admin_client.post(f"/tasks/{task.pk}/progress/", {"due_date": "2026-09-12", "task_baseline": baseline})
    assert response.status_code == 200
    assert "计划开始已调整" in response.content.decode()
    assert "阶段信息已更新" in response.content.decode()
    assert not ProgressUpdate.objects.exists()


def test_full_edit_schedule_only_preserves_stale_clock_but_real_change_refreshes_it(admin_client, task):
    stale = timezone.now() - timedelta(days=14)
    Task.objects.filter(pk=task.pk).update(updated_at=stale)
    response = admin_client.post(f"/tasks/{task.pk}/edit/", edit_payload(admin_client, task, planned_for="2026-09-11"))
    assert response.status_code == 302
    task.refresh_from_db()
    assert task.planned_for == date(2026, 9, 11)
    assert task.updated_at == stale
    assert not ProgressUpdate.objects.exists()
    response = admin_client.post(f"/tasks/{task.pk}/edit/", edit_payload(admin_client, task, current_note="接口已联通"))
    assert response.status_code == 302
    task.refresh_from_db()
    assert task.updated_at > stale


def test_full_edit_rejects_project_move_with_linked_risk_and_keeps_both_projects(admin_client, task):
    source_id = task.project_id
    risk = Risk.objects.create(project=task.project, task=task, content="验收风险")
    target = Project.objects.create(name="金蝶")
    response = admin_client.post(f"/tasks/{task.pk}/edit/", edit_payload(admin_client, task, project=target.pk))
    assert response.status_code == 200
    assert "先调整关联" in response.content.decode()
    task.refresh_from_db(); risk.refresh_from_db()
    assert task.project_id == risk.project_id == source_id


def test_full_edit_can_move_an_unlinked_task_but_cannot_retain_an_old_phase(admin_client, task):
    phase = ProjectPhase.objects.create(project=task.project, name="开发")
    task.phase = phase
    task.save()
    target = Project.objects.create(name="金蝶")
    rejected = admin_client.post(f"/tasks/{task.pk}/edit/", edit_payload(admin_client, task, project=target.pk))
    assert rejected.status_code == 200
    assert "阶段必须属于当前项目" in rejected.content.decode()
    accepted = admin_client.post(f"/tasks/{task.pk}/edit/", edit_payload(admin_client, task, project=target.pk, phase=""))
    assert accepted.status_code == 302
    task.refresh_from_db()
    assert task.project_id == target.pk
    assert task.phase_id is None


@pytest.mark.parametrize("related", ["risk", "phase"])
def test_draft_task_move_with_cross_project_links_is_rejected_without_500_or_writes(admin_client, task, related):
    source_id = task.project_id
    if related == "risk":
        Risk.objects.create(project=task.project, task=task, content="验收风险")
    else:
        task.phase = ProjectPhase.objects.create(project=task.project, name="开发")
        task.save()
    target = Project.objects.create(name="金蝶")
    note = MeetingNote.objects.create(title="更新", meeting_date=timezone.localdate(), raw_text="接口联调", parse_status="success")
    draft = ImportDraft.objects.create(meeting_note=note, payload={"tasks": [{"title": task.title}]})
    review = admin_client.get(f"/drafts/{draft.pk}/")
    response = admin_client.post(f"/drafts/{draft.pk}/confirm/", {
        "task_0_action": "update", "task_0_existing": task.pk, "task_0_project": target.pk,
        "review_baseline": review.context["review_baseline"],
        "review_version": review.context["draft"].review_version,
    })
    assert response.status_code == 200
    assert "先调整" in response.content.decode()
    task.refresh_from_db(); draft.refresh_from_db(); note.refresh_from_db()
    assert task.project_id == source_id
    assert draft.confirmed_at is None
    assert note.parse_status == "success"
    assert not ProgressUpdate.objects.exists()


def test_draft_invalid_task_dates_redisplay_validation_and_roll_back(admin_client, task):
    note = MeetingNote.objects.create(title="更新", meeting_date=date(2026, 9, 5), raw_text="接口联调", parse_status="success")
    draft = ImportDraft.objects.create(meeting_note=note, payload={"tasks": [{"title": task.title, "due_date": "2026-09-09"}]})
    response = admin_client.post(f"/drafts/{draft.pk}/confirm/", {
        "task_0_action": "update", "task_0_existing": task.pk, "task_0_project": task.project_id,
    })
    assert response.status_code == 200
    assert "截止日期不能早于计划开始" in response.content.decode()
    task.refresh_from_db(); draft.refresh_from_db()
    assert task.due_date == date(2026, 9, 16)
    assert draft.confirmed_at is None
