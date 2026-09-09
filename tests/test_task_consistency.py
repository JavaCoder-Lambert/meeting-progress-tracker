from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from core.models import Person, ProgressUpdate, Project, Risk, Task
from core.services.progress_updates import record_task_progress


pytestmark = pytest.mark.django_db


def edit_payload(client, task):
    form = client.get(reverse("task_edit", args=[task.pk])).context["form"]
    return {name: "" if form[name].value() is None else form[name].value() for name in form.fields}


@pytest.mark.parametrize("entry", ["create", "edit"])
@pytest.mark.parametrize("status,progress,expected_progress", [
    (Task.Status.DONE, 35, 100),
    (Task.Status.ACCEPTANCE, 100, 100),
    (Task.Status.IN_PROGRESS, 100, 100),
])
def test_task_editor_normalizes_done_without_inferring_status_from_progress(
    admin_client, entry, status, progress, expected_progress,
):
    project = Project.objects.create(name="订单履约")
    if entry == "edit":
        task = Task.objects.create(project=project, title="同步库存", progress=20)
        url = reverse("task_edit", args=[task.pk])
        payload = edit_payload(admin_client, task)
    else:
        url = reverse("task_create")
        payload = {"project": project.pk, "title": "同步库存", "priority": "normal"}

    response = admin_client.post(url, {**payload, "status": status, "progress": progress})

    assert response.status_code == 302
    saved = project.tasks.get()
    update = saved.progress_updates.get()
    assert (saved.status, saved.progress) == (status, expected_progress)
    assert (update.new_status, update.new_progress) == (status, expected_progress)
    assert update.snapshot["progress"] == expected_progress
    assert bool(saved.completed_at) == (status == Task.Status.DONE)


@pytest.mark.parametrize("initial_status,changes", [
    (Task.Status.IN_PROGRESS, {"status": Task.Status.DONE, "progress": 35}),
    (Task.Status.IN_PROGRESS, {"status": Task.Status.DONE}),
    (Task.Status.DONE, {"progress": 35}),
])
def test_progress_service_records_done_as_one_hundred(initial_status, changes):
    project = Project.objects.create(name="订单履约")
    task = Task.objects.create(project=project, title="同步库存", status=initial_status, progress=20)

    update = record_task_progress(task, changes)

    task.refresh_from_db()
    assert (task.status, task.progress) == (Task.Status.DONE, 100)
    assert (update.previous_progress, update.new_progress) == (20, 100)
    assert update.snapshot["progress"] == 100


@pytest.mark.parametrize("status,progress,expected_progress", [
    (Task.Status.DONE, 35, 100),
    (Task.Status.ACCEPTANCE, 100, 100),
    (Task.Status.IN_PROGRESS, 100, 100),
])
def test_progress_entry_preserves_explicit_status(admin_client, status, progress, expected_progress):
    project = Project.objects.create(name="订单履约")
    task = Task.objects.create(project=project, title="同步库存", progress=20)
    form = admin_client.get(reverse("task_detail", args=[task.pk])).context["progress_form"]

    response = admin_client.post(reverse("task_progress_update", args=[task.pk]), {
        "task_baseline": form["task_baseline"].value(), "status": status, "progress": progress,
        "completed_work": "接口联调完成", "next_step": "等待业务确认",
    })

    assert response.status_code == 302
    task.refresh_from_db()
    assert (task.status, task.progress) == (status, expected_progress)
    update = task.progress_updates.get()
    assert update.new_progress == expected_progress
    assert update.completed_work == "接口联调完成"
    assert update.next_step == "等待业务确认"
    assert bool(task.completed_at) == (status == Task.Status.DONE)


def test_edit_of_legacy_done_task_saves_normalized_progress_with_schedule_change(admin_client):
    project = Project.objects.create(name="订单履约")
    completed_at = timezone.now() - timedelta(days=10)
    task = Task.objects.create(
        project=project, title="历史任务", status=Task.Status.DONE, progress=35, completed_at=completed_at,
    )
    payload = edit_payload(admin_client, task)

    response = admin_client.post(reverse("task_edit", args=[task.pk]), {**payload, "planned_for": "2026-09-14"})

    assert response.status_code == 302
    task.refresh_from_db()
    assert task.progress == 100
    assert task.completed_at == completed_at
    assert task.progress_updates.get().new_progress == 100


def test_reading_legacy_task_does_not_rewrite_task_or_history(admin_client):
    project = Project.objects.create(name="历史项目")
    task = Task.objects.create(project=project, title="历史任务", status=Task.Status.DONE, progress=35)
    update = ProgressUpdate.objects.create(task=task, new_status=Task.Status.DONE, new_progress=35)

    assert admin_client.get(reverse("task_detail", args=[task.pk])).status_code == 200
    assert admin_client.get(reverse("task_edit", args=[task.pk])).status_code == 200
    assert admin_client.get("/").status_code == 200

    task.refresh_from_db()
    update.refresh_from_db()
    assert task.progress == 35
    assert task.completed_at is None
    assert update.new_progress == 35
    assert task.progress_updates.count() == 1


def test_dashboard_metrics_exclude_archived_projects(admin_client):
    for name, status in (("当前项目", Project.Status.IN_PROGRESS), ("历史项目", Project.Status.ARCHIVED)):
        project = Project.objects.create(name=name, status=status)
        for task_status in (Task.Status.IN_PROGRESS, Task.Status.ACCEPTANCE, Task.Status.DELAYED, Task.Status.BLOCKED):
            Task.objects.create(project=project, title=task_status, status=task_status)

    response = admin_client.get("/")

    assert response.context["metrics"] == {"in_progress": 1, "acceptance": 1, "delayed": 1, "blocked": 1}


@pytest.mark.parametrize("queue,context_key", [
    ("overdue", "overdue_tasks"), ("due_soon", "due_soon_tasks"), ("stale", "stale_tasks"),
])
def test_dashboard_queues_and_followups_exclude_archived_but_project_history_is_queryable(
    admin_client, queue, context_key,
):
    today = timezone.localdate()
    due_date = today - timedelta(days=1) if queue == "overdue" else today + timedelta(days=2) if queue == "due_soon" else None
    assignee = Person.objects.create(name="小李")
    project = Project.objects.create(name="当前项目")
    archived = Project.objects.create(name="历史项目", status=Project.Status.ARCHIVED)
    wanted = Task.objects.create(project=project, assignee=assignee, title="当前任务", due_date=due_date)
    historical = Task.objects.create(project=archived, assignee=assignee, title="历史任务", due_date=due_date)
    if queue == "stale":
        Task.objects.filter(pk__in=[wanted.pk, historical.pk]).update(updated_at=timezone.now() - timedelta(days=9))

    response = admin_client.get("/")

    assert list(response.context[context_key]) == [wanted]
    assert response.context["action_counts"][context_key] == 1
    if queue == "due_soon":
        assert list(response.context["due_tasks"]) == [wanted]
    groups = response.context["follow_up_groups"]
    assert len(groups) == 1
    assert groups[0]["tasks"] == [wanted]
    assert "历史任务" not in groups[0]["text"]
    assert list(admin_client.get(reverse("task_list"), {"queue": queue}).context["tasks"]) == [wanted]
    assert list(admin_client.get(reverse("task_list"), {"queue": queue, "project": archived.pk}).context["tasks"]) == [historical]
    assert admin_client.get(reverse("task_detail", args=[historical.pk])).status_code == 200


def test_dashboard_risks_exclude_archived_and_keep_risk_records(admin_client):
    project = Project.objects.create(name="当前项目")
    archived = Project.objects.create(name="历史项目", status=Project.Status.ARCHIVED)
    wanted = Risk.objects.create(project=project, content="当前风险")
    historical = Risk.objects.create(project=archived, content="历史风险")

    response = admin_client.get("/")

    assert list(response.context["open_risks"]) == [wanted]
    assert list(response.context["risks"]) == [wanted]
    assert response.context["action_counts"]["open_risks"] == 1
    assert Risk.objects.filter(pk=historical.pk).exists()
    assert historical.content in admin_client.get(reverse("project_detail", args=[archived.pk])).content.decode()


def test_archive_form_warns_about_unfinished_tasks_without_changing_them(admin_client):
    project = Project.objects.create(name="归档项目", progress=40)
    task = Task.objects.create(project=project, title="待推进", progress=40, status=Task.Status.IN_PROGRESS)
    waiting = Task.objects.create(project=project, title="待验收", progress=100, status=Task.Status.ACCEPTANCE)
    done = Task.objects.create(project=project, title="已完成", progress=100, status=Task.Status.DONE)
    risk = Risk.objects.create(project=project, content="待确认的风险")
    update = ProgressUpdate.objects.create(task=task, new_status=Task.Status.IN_PROGRESS, new_progress=40)
    before = list(project.tasks.order_by("pk").values("pk", "status", "progress", "completed_at", "updated_at"))
    url = reverse("project_edit", args=[project.pk])

    response = admin_client.get(url)

    assert "仍有 2 个未完成任务" in response.content.decode()
    response = admin_client.post(url, {"name": project.name, "status": Project.Status.ARCHIVED, "progress": 40})
    assert response.status_code == 302
    project.refresh_from_db()
    assert project.status == Project.Status.ARCHIVED
    assert list(project.tasks.order_by("pk").values("pk", "status", "progress", "completed_at", "updated_at")) == before
    assert Risk.objects.filter(pk=risk.pk).exists()
    assert ProgressUpdate.objects.get(pk=update.pk).new_progress == 40
    assert project.tasks.count() == 3
    assert task.progress_updates.count() == 1
