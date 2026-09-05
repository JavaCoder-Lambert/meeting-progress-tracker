import pytest
from datetime import date
from django.urls import reverse

from core.models import MeetingNote, ProgressUpdate, Project, Risk, Task
from core.services.progress_updates import record_task_progress


@pytest.mark.django_db
def test_text_only_progress_update_creates_history_without_changing_task_values():
    project = Project.objects.create(name="仓配升级")
    task = Task.objects.create(
        project=project,
        title="完成联调",
        status=Task.Status.IN_PROGRESS,
        progress=40,
    )

    update = record_task_progress(task, {"completed_work": "已完成接口联调"})

    task.refresh_from_db()
    assert ProgressUpdate.objects.get(pk=update.pk) == update
    assert update.previous_progress == 40
    assert update.new_progress == 40
    assert update.previous_status == Task.Status.IN_PROGRESS
    assert update.new_status == Task.Status.IN_PROGRESS
    assert update.completed_work == "已完成接口联调"
    assert task.progress == 40
    assert task.status == Task.Status.IN_PROGRESS


@pytest.mark.django_db
def test_invalid_progress_form_does_not_write_update_or_change_task(admin_client):
    project = Project.objects.create(name="仓配升级")
    task = Task.objects.create(project=project, title="完成联调", progress=40)

    response = admin_client.post(
        reverse("task_progress_update", args=[task.pk]),
        {"progress": "101", "completed_work": "不应写入"},
    )

    task.refresh_from_db()
    assert response.status_code == 200
    assert "请修正" in response.content.decode()
    assert ProgressUpdate.objects.count() == 0
    assert task.progress == 40


@pytest.mark.django_db
def test_text_only_form_submission_keeps_existing_task_values_and_redirects_to_detail(admin_client):
    project = Project.objects.create(name="仓配升级")
    task = Task.objects.create(
        project=project,
        title="完成联调",
        status=Task.Status.IN_PROGRESS,
        progress=40,
        due_date=date(2026, 9, 12),
    )

    response = admin_client.post(
        reverse("task_progress_update", args=[task.pk]),
        {"completed_work": "已完成接口联调", "next_step": "安排验收"},
    )

    task.refresh_from_db()
    update = ProgressUpdate.objects.get()
    assert response.status_code == 302
    assert response.url == reverse("task_detail", args=[task.pk])
    assert task.due_date == date(2026, 9, 12)
    assert (update.previous_progress, update.new_progress) == (40, 40)
    assert update.completed_work == "已完成接口联调"
    assert update.next_step == "安排验收"


@pytest.mark.django_db
def test_done_and_reopened_progress_updates_set_and_clear_completed_at():
    project = Project.objects.create(name="仓配升级")
    task = Task.objects.create(project=project, title="完成联调", status=Task.Status.IN_PROGRESS)

    done_update = record_task_progress(task, {"status": Task.Status.DONE, "progress": 100})
    task.refresh_from_db()
    completed_at = task.completed_at

    reopened_update = record_task_progress(task, {"status": Task.Status.IN_PROGRESS, "progress": 80})
    task.refresh_from_db()

    assert done_update.previous_status == Task.Status.IN_PROGRESS
    assert done_update.new_status == Task.Status.DONE
    assert completed_at is not None
    assert reopened_update.previous_status == Task.Status.DONE
    assert reopened_update.new_status == Task.Status.IN_PROGRESS
    assert task.completed_at is None


@pytest.mark.django_db
def test_task_detail_shows_source_meeting_related_risks_and_latest_progress(admin_client):
    project = Project.objects.create(name="仓配升级")
    meeting = MeetingNote.objects.create(title="周四联调会", meeting_date=date(2026, 9, 4), raw_text="记录")
    task = Task.objects.create(project=project, title="完成联调", source_meeting=meeting)
    Risk.objects.create(project=project, task=task, content="验收环境未就绪")
    record_task_progress(task, {"completed_work": "已完成接口联调", "next_step": "安排验收"})

    response = admin_client.get(reverse("task_detail", args=[task.pk]))

    content = response.content.decode()
    assert response.status_code == 200
    assert "记录本次进展" in content
    assert "周四联调会" in content
    assert "验收环境未就绪" in content
    assert "已完成接口联调" in content
    assert 'action="{}"'.format(reverse("task_progress_update", args=[task.pk])) in content
