import pytest

from core.models import Person, Project, Task
from core.services.task_matching import find_task_candidates


@pytest.mark.django_db
def test_candidate_match_does_not_update_task():
    project = Project.objects.create(name="SKU改造")
    person = Person.objects.create(name="张川")
    existing = Task.objects.create(project=project, assignee=person, title="发货仓库优先级逻辑调整")
    candidates = find_task_candidates(
        {"project_name": project.name, "assignee_name": person.name, "title": "完成发货仓库优先级逻辑调整"},
        Task.objects.all(),
    )
    existing.refresh_from_db()
    assert candidates[0].task_id == existing.id
    assert existing.title == "发货仓库优先级逻辑调整"


@pytest.mark.django_db
def test_unrelated_task_has_no_candidate():
    project = Project.objects.create(name="SKU改造")
    Task.objects.create(project=project, title="库存同步")
    assert find_task_candidates({"project_name": "SKU改造", "title": "财务月报"}, Task.objects.all()) == []
