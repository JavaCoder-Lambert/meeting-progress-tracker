from datetime import date, timedelta

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError

from core.models import Person, Project, Task


@pytest.fixture
def project(db):
    return Project.objects.create(name="金蝶对接")


def test_task_progress_is_limited_to_percentage(project):
    task = Task(project=project, title="接口联调", progress=101)
    with pytest.raises(ValidationError):
        task.full_clean()


def test_task_is_overdue_only_when_open_and_past_due(project):
    task = Task(project=project, title="接口联调", due_date=date.today() - timedelta(days=1))
    assert task.is_overdue is True
    task.status = Task.Status.DONE
    assert task.is_overdue is False


@pytest.mark.django_db(transaction=True)
def test_project_and_person_names_are_unique():
    Project.objects.create(name="金蝶")
    Person.objects.create(name="张川")
    with pytest.raises(IntegrityError):
        Project.objects.create(name="金蝶")
    with pytest.raises(IntegrityError):
        Person.objects.create(name="张川")
