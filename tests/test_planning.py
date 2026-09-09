from datetime import date, timedelta
import json

import pytest
from django.utils import timezone

from core.models import Milestone, Project, ProjectPhase, Risk, Task
from core.services.exports import build_json_export
from core.services.planning import plan_period, safe_date

pytestmark = pytest.mark.django_db


@pytest.fixture
def project():
    return Project.objects.create(name="订单履约改造")


def test_schedule_moves_work_without_changing_commitment_or_stale_clock(admin_client, project):
    task = Task.objects.create(project=project, title="联调", due_date=date(2026, 9, 16))
    previous = task.updated_at
    baseline = admin_client.get(f"/tasks/{task.pk}/").context["task_baseline"]
    response = admin_client.post(f"/tasks/{task.pk}/schedule/", {"action": "today", "next": "/plans/?view=today", "task_baseline": baseline})
    assert response.url == "/plans/?view=today"
    task.refresh_from_db()
    assert task.planned_for == timezone.localdate()
    assert task.due_date == date(2026, 9, 16)
    assert task.updated_at == previous
    assert not task.progress_updates.exists()
    assert task.title in admin_client.get("/plans/?view=today").content.decode()


def test_schedule_rejects_invalid_date_and_external_return(admin_client, project):
    task = Task.objects.create(project=project, title="联调")
    response = admin_client.post(f"/tasks/{task.pk}/schedule/", {"action": "date", "planned_for": "2026-02-30", "next": "https://evil.test"})
    assert response.url == "/plans/"
    task.refresh_from_db()
    assert task.planned_for is None
    assert admin_client.get(f"/tasks/{task.pk}/schedule/").status_code == 405


def test_week_rollover_retains_past_incomplete_and_excludes_future_and_done(admin_client, project):
    today = timezone.localdate()
    monday = today - timedelta(days=today.weekday())
    old = Task.objects.create(project=project, title="上周未完成", planned_for=monday - timedelta(days=3))
    Task.objects.create(project=project, title="上周已完成", planned_for=monday - timedelta(days=3), status="done")
    future = Task.objects.create(project=project, title="下周安排", planned_for=monday + timedelta(days=7))
    response = admin_client.get("/plans/?view=week")
    assert [t.pk for t in response.context["tasks"]] == [old.pk]
    assert response.context["tasks"][0].carried_over
    next_response = admin_client.get("/plans/?view=next")
    assert {t.pk for t in next_response.context["tasks"]} == {old.pk, future.pk}


def test_explicit_arrangement_and_clear_and_filter(admin_client, project):
    task = Task.objects.create(project=project, title="接口验收")
    other = Project.objects.create(name="财务")
    Task.objects.create(project=other, title="财务独立任务")
    baseline = admin_client.get(f"/tasks/{task.pk}/").context["task_baseline"]
    admin_client.post(f"/tasks/{task.pk}/schedule/", {"action": "date", "planned_for": "2026-09-08", "task_baseline": baseline})
    task.refresh_from_db()
    assert task.planned_for == date(2026, 9, 8)
    baseline = admin_client.get(f"/tasks/{task.pk}/").context["task_baseline"]
    admin_client.post(f"/tasks/{task.pk}/schedule/", {"action": "clear", "task_baseline": baseline})
    response = admin_client.get(f"/plans/?view=unscheduled&project={project.pk}")
    assert [t.pk for t in response.context["tasks"]] == [task.pk]


def test_phase_date_validation_and_project_task_association(admin_client, project):
    url = f"/projects/{project.pk}/phases/new/"
    invalid = {"name": "联调", "position": 1, "status": "in_progress", "start_date": "2026-09-15", "end_date": "2026-09-10"}
    assert "不能早于" in admin_client.post(url, invalid).content.decode()
    assert not ProjectPhase.objects.exists()
    assert admin_client.post(url, {**invalid, "end_date": "2026-09-20"}).status_code == 302
    phase = ProjectPhase.objects.get()
    other = Project.objects.create(name="其他项目")
    payload = {"project": other.pk, "phase": phase.pk, "title": "错误关联", "status": "not_started", "priority": "normal", "progress": 0}
    assert "必须属于" in admin_client.post("/tasks/new/", payload).content.decode()
    assert not Task.objects.exists()
    response = admin_client.post("/tasks/new/", {**payload, "project": project.pk, "planned_for": "2026-09-16"})
    assert response.status_code == 302
    assert Task.objects.get().phase == phase


def test_manual_milestone_and_risk_have_complete_edit_path(admin_client, project):
    response = admin_client.post(f"/projects/{project.pk}/milestones/new/", {"project": project.pk, "name": "内测", "target_date": "2026-09-16", "status": "not_started"})
    assert response.status_code == 302
    milestone = Milestone.objects.get()
    admin_client.post(f"/milestones/{milestone.pk}/edit/", {"project": project.pk, "name": "内测", "target_date": "2026-09-16", "status": "done"})
    milestone.refresh_from_db()
    assert milestone.status == "done"
    risk = Risk.objects.create(project=project, content="接口字段待确认")
    payload = {"project": project.pk, "content": risk.content, "risk_type": "risk", "status": "resolved"}
    assert admin_client.post(f"/risks/{risk.pk}/edit/", payload).status_code == 302
    risk.refresh_from_db()
    assert risk.resolved_at
    admin_client.post(f"/risks/{risk.pk}/edit/", {**payload, "status": "open"})
    risk.refresh_from_db()
    assert risk.resolved_at is None


def test_timeline_clamps_long_span_and_displays_unscheduled(admin_client, project):
    ProjectPhase.objects.create(project=project, name="长期开发", start_date=date(2020, 1, 1), end_date=date(2030, 1, 1))
    ProjectPhase.objects.create(project=project, name="待定验收")
    Milestone.objects.create(project=project, name="上线", target_date=date(2026, 9, 16))
    response = admin_client.get(f"/projects/{project.pk}/?tab=timeline&start=2026-09-05")
    assert response.status_code == 200
    rows = response.context["timeline_rows"]
    assert rows[0]["offset"] == 0 and rows[0]["width"] == 100
    assert not rows[1]["dated"]
    assert "待排期" in response.content.decode()
    assert "上线" in response.content.decode()


def test_plan_period_crosses_year_and_invalid_filters_do_not_crash(admin_client, project):
    start, end = plan_period("week", date(2027, 1, 1))
    assert (start, end) == (date(2026, 12, 28), date(2027, 1, 3))
    assert safe_date("9999-12-31") == timezone.localdate()
    assert admin_client.get("/plans/?view=bad&date=no&project=x&assignee=99999999999999999999999").status_code == 200
    assert admin_client.get("/tasks/?project=x").status_code == 200


def test_planning_pages_are_paginated_and_exports_include_phase(admin_client, project):
    phase = ProjectPhase.objects.create(project=project, name="开发")
    Task.objects.bulk_create([Task(project=project, phase=phase, title=f"任务 {i}") for i in range(40)])
    response = admin_client.get("/plans/?view=unscheduled")
    assert len(response.context["tasks"]) == 25
    assert response.context["page_obj"].paginator.count == 40
    assert len(admin_client.get("/tasks/").context["tasks"]) == 30
    exported = json.loads(build_json_export())
    assert exported["projectphase"][0]["name"] == "开发"
    assert exported["task"][0]["phase"] == phase.pk


def test_planning_views_require_authentication(client, project):
    assert client.get("/plans/").status_code == 302
    assert client.post(f"/projects/{project.pk}/phases/new/", {}).status_code == 302
