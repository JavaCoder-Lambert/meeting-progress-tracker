from datetime import date

import pytest
from django.test.utils import CaptureQueriesContext
from django.db import connection

from core.models import ImportDraft, MeetingNote, Person, Project, Task
from core.services.draft_review import build_draft_review


def make_draft(*, tasks):
    note = MeetingNote.objects.create(title="周会", meeting_date=date(2026, 9, 4), raw_text="记录")
    return ImportDraft.objects.create(
        meeting_note=note,
        payload={"summary": "", "uncertainties": [], "risks": [], "milestones": [], "tasks": tasks},
    )


def task_payload(title, **overrides):
    payload = {
        "title": title,
        "project_name": "SKU改造",
        "assignee_name": "张川",
        "description": "",
        "planned_start_date": None,
        "due_date": "2026-09-09",
        "acceptance_date": None,
        "status": "in_progress",
        "priority": "normal",
        "progress": 50,
        "current_note": "",
        "completed_work": "",
        "next_step": "",
    }
    return {**payload, **overrides}


@pytest.mark.django_db
def test_unmatched_project_defaults_to_ignore_and_needs_attention():
    draft = make_draft(tasks=[task_payload("联调", project_name="不存在的项目")])

    review = build_draft_review(draft)

    row = review["task_rows"][0]
    assert row["recommended_action"] == "ignore"
    assert row["action"] == "ignore"
    assert row["needs_attention"] is True
    assert "项目未匹配" in row["attention_reasons"]


@pytest.mark.django_db
def test_unique_exact_candidate_recommends_update_and_exposes_diffs():
    project = Project.objects.create(name="SKU改造")
    person = Person.objects.create(name="张川")
    existing = Task.objects.create(
        project=project,
        assignee=person,
        title="发货仓库优先级逻辑调整",
        progress=20,
        due_date=date(2026, 9, 8),
    )
    draft = make_draft(tasks=[task_payload("发货仓库优先级逻辑调整")])

    review = build_draft_review(draft)

    row = review["task_rows"][0]
    assert row["recommended_action"] == "update"
    assert row["action"] == "update"
    assert row["existing_id"] == str(existing.id)
    assert row["needs_attention"] is False
    assert {diff["field"] for diff in row["diffs"]} >= {"progress", "due_date"}


@pytest.mark.django_db
def test_title_substring_candidate_needs_attention_instead_of_automatic_update():
    project = Project.objects.create(name="SKU改造")
    person = Person.objects.create(name="张川")
    Task.objects.create(project=project, assignee=person, title="账单重构")
    draft = make_draft(tasks=[task_payload("账单")])

    review = build_draft_review(draft)

    row = review["task_rows"][0]
    assert row["recommended_action"] == "ignore"
    assert row["needs_attention"] is True
    assert "疑似重复" in row["attention_reasons"]


@pytest.mark.django_db
def test_mapped_task_without_candidate_recommends_create():
    Project.objects.create(name="SKU改造")
    Person.objects.create(name="张川")
    draft = make_draft(tasks=[task_payload("新建联调任务")])

    review = build_draft_review(draft)

    row = review["task_rows"][0]
    assert row["recommended_action"] == "create"
    assert row["action"] == "create"
    assert row["needs_attention"] is False


@pytest.mark.django_db
def test_review_evaluates_open_tasks_once_for_multiple_draft_rows():
    project = Project.objects.create(name="SKU改造")
    person = Person.objects.create(name="张川")
    Task.objects.create(project=project, assignee=person, title="已有联调")
    draft = make_draft(tasks=[task_payload("第一项"), task_payload("第二项")])

    with CaptureQueriesContext(connection) as queries:
        build_draft_review(draft)

    open_task_queries = [query["sql"] for query in queries.captured_queries if '"core_task"' in query["sql"]]
    assert len(open_task_queries) == 1


@pytest.mark.django_db
def test_review_counts_actions_and_attention():
    project = Project.objects.create(name="SKU改造")
    person = Person.objects.create(name="张川")
    Task.objects.create(project=project, assignee=person, title="已有任务")
    draft = make_draft(tasks=[
        task_payload("已有任务"),
        task_payload("新任务"),
        task_payload("待确认", project_name="未匹配项目"),
    ])

    review = build_draft_review(draft)

    assert review["review_counts"] == {"create": 1, "update": 1, "ignore": 1, "attention": 1}


@pytest.mark.django_db
def test_review_handles_short_dates_using_the_meeting_date():
    Project.objects.create(name="SKU改造")
    Person.objects.create(name="张川")
    draft = make_draft(tasks=[task_payload("联调", planned_start_date="0907")])

    review = build_draft_review(draft)

    assert review["task_rows"][0]["planned_start_date"]["value"] == "2026-09-07"
    assert review["task_rows"][0]["needs_attention"] is False


@pytest.mark.django_db
def test_review_page_uses_compact_attention_first_rows_and_preserves_task_fields(admin_client):
    draft = make_draft(tasks=[task_payload("联调", project_name="未匹配项目")])

    response = admin_client.get(f"/drafts/{draft.id}/")

    content = response.content.decode()
    assert '<details class="review-item" open' in content
    assert "项目未匹配" in content
    assert 'data-review-filter="attention"' in content
    assert 'data-batch-action="recommended"' in content
    assert 'name="task_0_action"' in content
    assert 'name="task_0_project"' in content
    assert 'name="task_0_assignee"' in content
    assert 'name="task_0_existing"' in content
    assert 'name="task_0_title"' in content
    assert 'name="task_0_description"' in content
    assert 'name="task_0_progress"' in content
    assert 'name="task_0_planned_start_date"' in content
    assert 'name="task_0_due_date"' in content
    assert 'name="task_0_acceptance_date"' in content
    assert 'name="task_0_completed_work"' in content
    assert 'name="task_0_next_step"' in content
    assert 'name="task_0_current_note"' in content


@pytest.mark.django_db
def test_review_page_shows_import_summary_with_predicted_counts(admin_client):
    project = Project.objects.create(name="SKU改造")
    person = Person.objects.create(name="张川")
    Task.objects.create(project=project, assignee=person, title="已有任务")
    draft = make_draft(tasks=[task_payload("已有任务"), task_payload("新任务")])

    response = admin_client.get(f"/drafts/{draft.id}/")

    content = response.content.decode()
    assert "预计新增 1 项" in content
    assert "更新 1 项" in content
    assert "忽略 0 项" in content
