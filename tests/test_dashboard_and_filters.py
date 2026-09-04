import pytest

from core.models import Person, Project, Task


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
