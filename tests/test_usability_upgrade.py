from datetime import timedelta
from html.parser import HTMLParser

import pytest
from django.utils import timezone

from core.models import Person, Project, Task

pytestmark = pytest.mark.django_db


def test_week_plan_surfaces_due_work_without_silently_scheduling_it(admin_client):
    today = timezone.localdate()
    project = Project.objects.create(name="履约")
    task = Task.objects.create(project=project, title="等待安排的联调", due_date=today)
    response = admin_client.get("/plans/?view=week")
    assert task.title in response.content.decode()
    assert f'action="/tasks/{task.pk}/schedule/"' in response.content.decode()
    assert list(response.context["tasks"]) == []
    task.refresh_from_db()
    assert task.planned_for is None
    baseline = response.context["unscheduled_due"][0].edit_baseline
    admin_client.post(f"/tasks/{task.pk}/schedule/", {"action": "today", "next": "/plans/?view=week", "task_baseline": baseline})
    response = admin_client.get("/plans/?view=week")
    assert list(response.context["tasks"]) == [task]
    assert list(response.context["unscheduled_due"]) == []


def test_due_suggestions_respect_filters_period_and_completion(admin_client):
    today = timezone.localdate()
    project = Project.objects.create(name="履约")
    other = Project.objects.create(name="财务")
    person = Person.objects.create(name="张川")
    wanted = Task.objects.create(project=project, assignee=person, title="待安排", due_date=today)
    for values in ({"project": other}, {"assignee": None}, {"status": "done"},
                   {"planned_for": today}, {"due_date": today + timedelta(days=30)}):
        Task.objects.create(**{"project": project, "assignee": person, "title": "排除项", "due_date": today, **values})
    response = admin_client.get("/plans/", {"view": "today", "project": project.pk, "assignee": person.pk})
    assert [task.pk for task in response.context["unscheduled_due"]] == [wanted.pk]
    assert admin_client.get("/plans/?view=unscheduled").context["unscheduled_due"] == []


class Sections(HTMLParser):
    def __init__(self):
        super().__init__()
        self.elements = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get("id"):
            self.elements[attrs["id"]] = (tag, attrs)


def test_empty_dashboard_queues_are_collapsed_but_nonempty_work_is_visible(admin_client):
    response = admin_client.get("/")
    parser = Sections()
    parser.feed(response.content.decode())
    tag, attrs = parser.elements["quiet-queues"]
    assert tag == "details" and "open" not in attrs
    project = Project.objects.create(name="履约")
    Task.objects.create(project=project, title="逾期需要跟进", due_date=timezone.localdate() - timedelta(days=1))
    html = admin_client.get("/").content.decode()
    parser = Sections()
    parser.feed(html)
    assert parser.elements["overdue-queue"][0] == "section"
    assert "逾期需要跟进" in html
