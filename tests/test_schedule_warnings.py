from datetime import timedelta

from django.contrib.messages import get_messages
from django.utils import timezone

from core.models import Project, Task
from core.services.task_editing import task_edit_baseline


def test_late_arrangement_saves_and_warns_without_rewriting_deadline(admin_client):
    today = timezone.localdate()
    task = Task.objects.create(project=Project.objects.create(name="排期"), title="准备交付", due_date=today)
    response = admin_client.post(f"/tasks/{task.pk}/schedule/", {
        "task_baseline": task_edit_baseline(task), "action": "date",
        "planned_for": (today + timedelta(days=1)).isoformat(),
    })
    task.refresh_from_db()
    assert task.planned_for == today + timedelta(days=1)
    assert task.due_date == today
    assert any("晚于" in str(message) for message in get_messages(response.wsgi_request))
    assert "晚于交付截止" in admin_client.get(f"/tasks/{task.pk}/").content.decode()
    assert "晚于交付截止" in admin_client.get("/plans/").content.decode()


def test_done_or_on_time_arrangement_has_no_conflict(db):
    today = timezone.localdate()
    task = Task(project=Project.objects.create(name="正常"), title="正常交付", due_date=today, planned_for=today)
    assert not task.schedule_after_deadline
    task.planned_for += timedelta(days=1)
    assert task.schedule_after_deadline
    task.status = "done"
    assert not task.schedule_after_deadline
