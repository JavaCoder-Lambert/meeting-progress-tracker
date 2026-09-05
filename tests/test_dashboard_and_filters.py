from datetime import date, datetime, timedelta

import pytest
from django.utils import timezone

from core.models import ImportDraft, MeetingNote, Person, Project, Risk, Task


@pytest.mark.django_db
def test_task_filter_combines_project_status_and_assignee(admin_client):
    project = Project.objects.create(name="SKU")
    other = Project.objects.create(name="DSS")
    person = Person.objects.create(name="张川")
    wanted = Task.objects.create(project=project, assignee=person, title="联调", status=Task.Status.IN_PROGRESS)
    Task.objects.create(project=other, title="月报", status=Task.Status.IN_PROGRESS)
    response = admin_client.get("/tasks/", {"project": project.id, "assignee": person.id, "status": Task.Status.IN_PROGRESS})
    assert response.status_code == 200
    assert list(response.context["tasks"]) == [wanted]


@pytest.mark.django_db
def test_task_filter_includes_priority_and_preserves_selection(admin_client):
    project = Project.objects.create(name="SKU")
    urgent = Task.objects.create(project=project, title="阻塞项", priority=Task.Priority.URGENT)
    Task.objects.create(project=project, title="常规项", priority=Task.Priority.NORMAL)

    response = admin_client.get("/tasks/", {"priority": Task.Priority.URGENT})

    assert list(response.context["tasks"]) == [urgent]
    assert response.context["filters"]["priority"] == Task.Priority.URGENT
    content = response.content.decode()
    assert 'value="urgent" selected' in content


@pytest.mark.django_db
def test_dashboard_counts_blocked_tasks(admin_client):
    project = Project.objects.create(name="SKU")
    Task.objects.create(project=project, title="联调", status=Task.Status.BLOCKED)
    response = admin_client.get("/")
    assert response.context["metrics"]["blocked"] == 1


@pytest.mark.django_db
def test_user_can_create_project(admin_client):
    response = admin_client.post("/projects/new/", {"name": "金蝶", "status": "in_progress", "progress": 20})
    assert response.status_code == 302
    assert Project.objects.filter(name="金蝶").exists()


@pytest.mark.django_db
def test_dashboard_action_queues_are_mutually_exclusive_and_exclude_completed_tasks(admin_client, monkeypatch):
    today = date(2026, 9, 5)
    monkeypatch.setattr("core.services.dashboard.timezone.localdate", lambda: today)
    project = Project.objects.create(name="SKU")
    overdue = Task.objects.create(project=project, title="已逾期", due_date=today - timedelta(days=1))
    due_soon = Task.objects.create(project=project, title="本周到期", due_date=today + timedelta(days=7))
    stale = Task.objects.create(project=project, title="长期未更新")
    done = Task.objects.create(project=project, title="已完成", due_date=today - timedelta(days=2), status=Task.Status.DONE)
    Task.objects.filter(pk=stale.pk).update(updated_at=timezone.make_aware(datetime(2026, 8, 27)))
    note = MeetingNote.objects.create(title="待确认", meeting_date=today, raw_text="原文")
    draft = ImportDraft.objects.create(meeting_note=note, payload={})
    Risk.objects.create(project=project, content="阻塞风险")

    response = admin_client.get("/")

    assert response.status_code == 200
    context = response.context
    assert list(context["pending_drafts"]) == [draft]
    assert list(context["overdue_tasks"]) == [overdue]
    assert list(context["due_soon_tasks"]) == [due_soon]
    assert list(context["stale_tasks"]) == [stale]
    assert done not in context["overdue_tasks"]
    assert done not in context["due_soon_tasks"]
    assert context["action_counts"] == {
        "pending_drafts": 1, "overdue_tasks": 1, "due_soon_tasks": 1,
        "stale_tasks": 1, "open_risks": 1,
    }


@pytest.mark.django_db
def test_dashboard_builds_deterministic_dingtalk_ready_follow_up_by_assignee(admin_client, monkeypatch):
    today = date(2026, 9, 5)
    monkeypatch.setattr("core.services.dashboard.timezone.localdate", lambda: today)
    project = Project.objects.create(name="SKU")
    assignee = Person.objects.create(name="张川")
    Task.objects.create(project=project, assignee=assignee, title="接口联调", due_date=today - timedelta(days=1))
    Task.objects.create(project=project, assignee=assignee, title="准备验收", due_date=today + timedelta(days=2))

    response = admin_client.get("/")

    groups = response.context["follow_up_groups"]
    assert len(groups) == 1
    assert groups[0]["person"] == assignee
    assert groups[0]["text"] == (
        "张川，以下事项请跟进：\n"
        "1. 【SKU】接口联调（已逾期，截止 2026-09-04）\n"
        "2. 【SKU】准备验收（7 天内到期，截止 2026-09-07）"
    )
    content = response.content.decode()
    assert 'href="/meetings/?state=pending"' in content
    assert 'href="/tasks/?queue=overdue"' in content
    assert "按负责人跟进" in content
    assert "张川，以下事项请跟进：" in content
