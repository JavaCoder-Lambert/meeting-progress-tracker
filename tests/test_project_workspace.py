from datetime import date

import pytest

from core.models import MeetingSession, Person, Project, ProjectPhase, Task

pytestmark = pytest.mark.django_db


def test_project_groups_tasks_by_phase_and_keeps_completed_optional(admin_client):
    project = Project.objects.create(name="履约改造")
    phase = ProjectPhase.objects.create(project=project, name="开发")
    task = Task.objects.create(project=project, phase=phase, title="联调接口")
    loose = Task.objects.create(project=project, title="整理验收")
    done = Task.objects.create(project=project, phase=phase, title="旧开发", status="done")
    response = admin_client.get(f"/projects/{project.pk}/")
    groups = response.context["task_groups"]
    assert [(g["phase"].pk if g["phase"] else None, [t.pk for t in g["tasks"]]) for g in groups] == [(phase.pk, [task.pk]), (None, [loose.pk])]
    assert f'/meetings/manual/new/?project={project.pk}' in response.content.decode()
    all_response = admin_client.get(f"/projects/{project.pk}/?show_done=1")
    assert done.pk in [t.pk for g in all_response.context["task_groups"] for t in g["tasks"]]


def test_project_meeting_prepares_current_unfinished_tasks_only_after_submit(admin_client):
    person = Person.objects.create(name="张川")
    project = Project.objects.create(name="履约改造")
    other = Project.objects.create(name="其他项目")
    task = Task.objects.create(project=project, assignee=person, title="联调", progress=40, due_date=date(2026, 9, 16))
    Task.objects.create(project=project, title="旧任务", status="done")
    Task.objects.create(project=other, title="别的任务")
    url = f"/meetings/manual/new/?project={project.pk}"
    response = admin_client.get(url)
    assert response.context["form"].initial["projects"] == [project.pk]
    assert response.context["form"].initial["people"] == [person.pk]
    assert not MeetingSession.objects.exists()
    response = admin_client.post(url, {"title": "履约周会", "meeting_date": "2026-09-09", "projects": [project.pk], "people": [person.pk], "include_project_tasks": "on"})
    assert response.status_code == 302
    session = MeetingSession.objects.get()
    assert len(session.state["items"]) == 1
    card = session.state["items"][0]
    assert card["task_id"] == task.pk and card["progress"] == 40
    assert card["recorded"] is False
    assert card["completed_work"] == "" and card["next_step"] == ""
    assert not task.progress_updates.exists()
    workspace = admin_client.get(response.url)
    assert '<details class="mw-catalog">' in workspace.content.decode()


@pytest.mark.parametrize("keep_project,include_tasks", [(False, True), (True, False)])
def test_project_meeting_respects_deselection(admin_client, keep_project, include_tasks):
    project = Project.objects.create(name="订单")
    Task.objects.create(project=project, title="联调")
    response = admin_client.post(f"/meetings/manual/new/?project={project.pk}", {
        "title": "自选会议", "meeting_date": "2026-09-09", "projects": [project.pk] if keep_project else [],
        **({"include_project_tasks": "on"} if include_tasks else {}),
    })
    assert response.status_code == 302
    assert MeetingSession.objects.get().state["items"] == []


def test_schedule_form_from_plan_board_does_not_overwrite_newer_arrangement(admin_client):
    project = Project.objects.create(name="项目")
    task = Task.objects.create(project=project, title="推进")
    page = admin_client.get("/plans/?view=unscheduled")
    baseline = page.context["tasks"][0].edit_baseline
    assert f'value="{baseline}"' in page.content.decode()
    task.planned_for = date(2026, 9, 17)
    Task.objects.filter(pk=task.pk).update(planned_for=task.planned_for)
    response = admin_client.post(f"/tasks/{task.pk}/schedule/", {
        "action": "date", "planned_for": "2026-09-18", "task_baseline": baseline,
        "next": "/plans/?view=unscheduled",
    }, follow=True)
    task.refresh_from_db()
    assert task.planned_for == date(2026, 9, 17)
    assert "任务已变化" in response.content.decode()
