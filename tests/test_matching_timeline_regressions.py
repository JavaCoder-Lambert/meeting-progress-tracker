from datetime import date

import pytest

from core.models import ImportDraft, MeetingNote, Person, Project, Task
from core.services.draft_review import build_draft_review

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("completed", [False, True])
def test_reassignment_and_completed_task_remain_reviewable_candidates(completed):
    project = Project.objects.create(name="订单履约")
    previous = Person.objects.create(name="张川")
    incoming = Person.objects.create(name="钱增")
    task = Task.objects.create(project=project, assignee=previous, title="仓库优先级调整",
                               status="done" if completed else "in_progress", progress=100 if completed else 40)
    note = MeetingNote.objects.create(title="周会", meeting_date=date(2026, 9, 9), raw_text="交接")
    draft = ImportDraft.objects.create(meeting_note=note, payload={"tasks": [{
        "title": task.title, "project_name": project.name, "assignee_name": incoming.name,
        "status": "in_progress", "progress": 60,
    }]})
    row = build_draft_review(draft)["task_rows"][0]
    assert [candidate.task_id for candidate in row["candidates"]] == [task.pk]
    assert row["action"] == "ignore"
    assert row["existing_id"] == str(task.pk)
    assert "负责人变更，需确认" in row["attention_reasons"]
    if completed:
        assert "任务已完成，需确认是否继续更新" in row["attention_reasons"]
    task.refresh_from_db()
    assert task.assignee_id == previous.pk


def test_completed_exact_match_is_not_automatically_reopened():
    project = Project.objects.create(name="报表")
    task = Task.objects.create(project=project, title="费用校验", status="done", progress=100)
    note = MeetingNote.objects.create(title="周会", meeting_date=date(2026, 9, 9), raw_text="费用校验")
    draft = ImportDraft.objects.create(meeting_note=note, payload={"tasks": [{
        "title": task.title, "project_name": project.name, "progress": 50,
    }]})
    row = build_draft_review(draft)["task_rows"][0]
    assert row["action"] == "ignore"
    assert row["existing_id"] == str(task.pk)
    assert row["needs_attention"]


def test_timeline_filters_window_before_paginating(admin_client):
    project = Project.objects.create(name="长期项目")
    Task.objects.bulk_create([Task(project=project, title=f"旧任务 {i}", due_date=date(2020, 1, 1)) for i in range(110)])
    Task.objects.create(project=project, title="未排期")
    current = Task.objects.create(project=project, title="本窗口交付", due_date=date(2026, 9, 16))
    response = admin_client.get(f"/projects/{project.pk}/?tab=timeline&start=2026-09-09")
    tasks = [row["item"].pk for row in response.context["timeline_rows"] if row["kind"] == "task"]
    assert tasks == [current.pk]
    assert response.context["timeline_unscheduled_count"] == 1


def test_timeline_has_reachable_second_page_and_includes_spanning_tasks(admin_client):
    project = Project.objects.create(name="排期密集")
    spanning = Task.objects.create(project=project, title="跨窗口", planned_start_date=date(2026, 1, 1), due_date=date(2027, 1, 1))
    Task.objects.bulk_create([Task(project=project, title=f"任务 {i:03}", due_date=date(2026, 9, 16)) for i in range(101)])
    first = admin_client.get(f"/projects/{project.pk}/?tab=timeline&start=2026-09-09")
    second = admin_client.get(f"/projects/{project.pk}/?tab=timeline&start=2026-09-09&page=2")
    task_ids = lambda response: {row["item"].pk for row in response.context["timeline_rows"] if row["kind"] == "task"}
    assert first.context["page_obj"].paginator.count == 102
    assert len(task_ids(first)) == 100
    assert len(task_ids(second)) == 2
    assert task_ids(first).isdisjoint(task_ids(second))
    assert spanning.pk in task_ids(first) | task_ids(second)
