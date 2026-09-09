from datetime import date

import pytest
from django.urls import reverse

from core.models import ImportDraft, MeetingNote, Person, Project, Task


@pytest.fixture
def draft(db):
    note = MeetingNote.objects.create(title="周会", meeting_date=date(2026, 9, 4), raw_text="会议原文", parse_status="success")
    return ImportDraft.objects.create(meeting_note=note, payload={
        "summary": "摘要", "uncertainties": [],
        "tasks": [{"title": "接口开发", "project_name": "新项目", "assignee_name": "新同事",
                   "status": "in_progress", "priority": "normal", "progress": 50}],
        "risks": [{"content": "时间待定", "project_name": "新项目", "owner_name": "新同事"}],
        "milestones": [{"name": "上线", "project_name": "新项目"}],
    })


def submission(client, draft, **changes):
    page = client.get(f"/drafts/{draft.pk}/")
    return {"review_baseline": page.context["review_baseline"],
            "review_version": getattr(draft, "review_version", 0),
            "task_0_action": "create", "task_0_title": "改过的接口标题",
            "task_0_project": "", "task_0_assignee": "", "task_0_progress": "0",
            "task_0_status": "not_started", "task_0_due_date": "日期稍后确认",
            "task_0_next_step": "先补齐项目和人员", "risks_0_action": "create",
            "risks_0_due_date": "下周再定", "milestones_0_action": "create",
            "milestones_0_target_date": "", **changes}


def save(client, draft, data):
    return client.post(f"/drafts/{draft.pk}/save/", data, HTTP_X_REQUESTED_WITH="XMLHttpRequest")


def test_save_incomplete_review_restores_edits_and_choices_without_import(admin_client, draft):
    result = save(admin_client, draft, submission(admin_client, draft))
    assert result.status_code == 200
    assert result.json()["version"] == 1
    draft.refresh_from_db()
    assert draft.payload["tasks"][0]["title"] == "接口开发"
    assert not Task.objects.exists()
    assert draft.confirmed_at is None
    page = admin_client.get(f"/drafts/{draft.pk}/")
    row = page.context["task_rows"][0]
    assert row["item"]["title"] == "改过的接口标题"
    assert row["item"]["progress"] == 0
    assert row["item"]["status"] == "not_started"
    assert row["item"]["next_step"] == "先补齐项目和人员"
    assert row["action"] == "create"
    assert row["project_id"] == ""
    assert row["due_date"] == {"type": "text", "value": "日期稍后确认"}
    assert page.context["risk_rows"][0]["action"] == "create"
    assert page.context["risk_rows"][0]["due_date"]["value"] == "下周再定"
    assert page.context["milestone_rows"][0]["target_date"]["value"] == ""


def test_stale_tab_cannot_overwrite_saved_review_or_confirm(admin_client, draft):
    data = submission(admin_client, draft)
    assert save(admin_client, draft, data).status_code == 200
    assert save(admin_client, draft, {**data, "task_0_title": "旧页覆盖"}).status_code == 409
    assert admin_client.post(f"/drafts/{draft.pk}/confirm/", data).status_code == 409
    assert admin_client.get(f"/drafts/{draft.pk}/").context["task_rows"][0]["item"]["title"] == "改过的接口标题"
    assert not Task.objects.exists()


@pytest.mark.parametrize("kind,field,name,route,form", [
    ("project", "task_0_project", "新项目", "project_create", {"status": "not_started", "progress": 0}),
    ("person", "task_0_assignee", "新同事", "person_create", {"is_active": "on"}),
])
def test_save_create_reference_and_return_keeps_review(admin_client, draft, kind, field, name, route, form):
    result = admin_client.post(f"/drafts/{draft.pk}/save/", submission(admin_client, draft, destination=f"{kind}:{field}"))
    assert result.status_code == 302
    assert result.url.startswith(reverse(route) + "?")
    create = admin_client.get(result.url)
    assert create.context["form"]["name"].value() == name
    # Invalid creation must keep the return context and previously saved edits.
    invalid = admin_client.post(result.url, {**form, "name": ""})
    assert invalid.status_code == 200
    created = admin_client.post(result.url, {**form, "name": name})
    assert created.status_code == 302
    assert created.url == f"/drafts/{draft.pk}/?focus=0#review-task-0"
    page = admin_client.get(created.url)
    row = page.context["task_rows"][0]
    assert row["focused"] is True
    assert row["item"]["title"] == "改过的接口标题"
    assert row["due_date"]["value"] == "日期稍后确认"
    expected = Project.objects.get(name=name).pk if kind == "project" else Person.objects.get(name=name).pk
    assert row["project_id" if kind == "project" else "assignee_id"] == str(expected)
    assert not Task.objects.exists()


def test_saved_review_requires_final_confirmation_and_preserves_explicit_zero(admin_client, draft):
    from django.utils import timezone
    draft.meeting_note.meeting_date = timezone.localdate()
    draft.meeting_note.save(update_fields=["meeting_date"])
    project = Project.objects.create(name="新项目")
    person = Person.objects.create(name="新同事")
    Task.objects.create(project=project, assignee=person, title="接口开发", progress=60, status="in_progress")
    task = Task.objects.get()
    data = submission(admin_client, draft, task_0_action="update", task_0_existing=task.pk,
                      task_0_project=project.pk, task_0_due_date="", risks_0_action="ignore",
                      milestones_0_action="ignore")
    assert save(admin_client, draft, data).status_code == 200
    task.refresh_from_db()
    assert task.progress == 60
    draft.refresh_from_db()
    page = admin_client.get(f"/drafts/{draft.pk}/")
    assert page.context["task_rows"][0]["existing_id"] == str(task.pk)
    assert any(c.task_id == task.pk for c in page.context["task_rows"][0]["candidates"])
    data.update(review_version=draft.review_version, review_baseline=page.context["review_baseline"])
    response = admin_client.post(f"/drafts/{draft.pk}/confirm/", data)
    assert response.status_code == 302
    task.refresh_from_db()
    assert task.title == "改过的接口标题"
    assert task.progress == 0
    assert task.status == "not_started"
    assert save(admin_client, draft, data).status_code == 409


def test_save_auth_csrf_and_replaced_draft_guards(client, admin_client, draft):
    from django.test import Client
    data = submission(admin_client, draft)
    assert Client().post(f"/drafts/{draft.pk}/save/", data).status_code == 302
    ImportDraft.objects.create(meeting_note=draft.meeting_note, payload=draft.payload)
    assert save(admin_client, draft, data).status_code == 409


def test_failed_confirmation_is_durable_not_only_error_page(admin_client, draft):
    data = submission(admin_client, draft)
    result = admin_client.post(f"/drafts/{draft.pk}/confirm/", data)
    assert result.status_code == 200
    page = admin_client.get(f"/drafts/{draft.pk}/")
    assert page.context["task_rows"][0]["item"]["title"] == "改过的接口标题"
    assert page.context["task_rows"][0]["action"] == "create"


def test_stashed_review_is_discoverable_in_meeting_inbox(admin_client, draft):
    save(admin_client, draft, submission(admin_client, draft))
    page = admin_client.get("/meetings/?state=pending")
    assert "继续暂存草稿" in page.content.decode()
    assert "已暂存" in admin_client.get(f"/meetings/{draft.meeting_note_id}/").content.decode()


def test_csrf_and_malformed_return_token_are_rejected(admin_client, admin_user, draft):
    from django.test import Client
    secured = Client(enforce_csrf_checks=True)
    secured.force_login(admin_user)
    assert secured.post(f"/drafts/{draft.pk}/save/", {"review_version": 0}).status_code == 403
    assert admin_client.get("/projects/new/?review=forged").status_code == 400


def test_create_return_cannot_overwrite_edits_made_in_another_tab(admin_client, draft):
    data = submission(admin_client, draft, destination="project:task_0_project")
    create_url = admin_client.post(f"/drafts/{draft.pk}/save/", data).url
    draft.refresh_from_db()
    save(admin_client, draft, submission(admin_client, draft, task_0_title="另一个页面的新内容"))
    response = admin_client.post(create_url, {"name": "新项目", "status": "not_started", "progress": 0})
    assert response.status_code == 302
    row = admin_client.get(response.url).context["task_rows"][0]
    assert row["item"]["title"] == "另一个页面的新内容"
    assert row["project_id"] == ""
    assert Project.objects.filter(name="新项目").exists()


def test_successful_capture_ack_is_tied_to_user_note_and_only_sent_once(admin_client):
    token = "capture-unique-token"
    result = admin_client.post("/meetings/new/", {"title": "临时记录", "meeting_date": "2026-09-04", "raw_text": "原文", "capture_draft_token": token, "intent": "save"})
    page = admin_client.get(result.url)
    assert page.context["capture_saved_token"] == token
    assert admin_client.get(result.url).context["capture_saved_token"] == ""


def test_replaced_draft_confirmation_keeps_unsaved_values_visible(admin_client, draft):
    save(admin_client, draft, submission(admin_client, draft))
    draft.refresh_from_db()
    data = submission(admin_client, draft, task_0_title="切换前还没保存的内容")
    ImportDraft.objects.create(meeting_note=draft.meeting_note, payload=draft.payload)
    response = admin_client.post(f"/drafts/{draft.pk}/confirm/", data)
    assert response.context["task_rows"][0]["item"]["title"] == "切换前还没保存的内容"
    assert response.context["review_blocked"] is True
    assert not Task.objects.exists()


@pytest.mark.parametrize("action", ["save", "confirm"])
def test_native_stale_submission_displays_user_input_without_overwriting(admin_client, draft, action):
    data = submission(admin_client, draft)
    save(admin_client, draft, {**data, "task_0_title": "另一个标签保存的内容"})
    response = admin_client.post(f"/drafts/{draft.pk}/{action}/", {**data, "task_0_title": "旧标签未保存的内容"})
    assert response.status_code == 409
    assert response.context["task_rows"][0]["item"]["title"] == "旧标签未保存的内容"
    assert response.context["review_blocked"] is True
    assert admin_client.get(f"/drafts/{draft.pk}/").context["task_rows"][0]["item"]["title"] == "另一个标签保存的内容"


def test_stashed_person_still_renders_as_selected_after_being_deactivated(admin_client, draft):
    person = Person.objects.create(name="新同事")
    save(admin_client, draft, submission(admin_client, draft, task_0_assignee=person.pk))
    person.is_active = False
    person.save()
    page = admin_client.get(f"/drafts/{draft.pk}/")
    assert person.pk in [p.pk for p in page.context["people"]]
    assert page.context["task_rows"][0]["assignee_id"] == str(person.pk)


def test_confirmed_by_another_tab_keeps_current_submission_visible(admin_client, draft):
    from django.utils import timezone
    data = submission(admin_client, draft, task_0_title="尚未保存的新输入")
    draft.confirmed_at = timezone.now()
    draft.save()
    response = admin_client.post(f"/drafts/{draft.pk}/confirm/", data)
    assert response.context["task_rows"][0]["item"]["title"] == "尚未保存的新输入"
    assert response.context["review_blocked"] is True
    draft.refresh_from_db()
    assert draft.payload["tasks"][0]["title"] == "接口开发"
