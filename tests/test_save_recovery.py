"""A lost response must acknowledge the committed revision without another write."""
from copy import deepcopy

import pytest
from django.utils import timezone

from core.models import ImportDraft, MeetingNote, Task
from core.services.manual_meetings import confirm_session, create_session


@pytest.fixture
def manual_request(db):
    session = create_session({"title": "周会", "meeting_date": timezone.localdate().isoformat(), "items": []})
    return session, {"version": 0, "state": {**session.state, "title": "  已保存的修改  "}}


def save_manual(client, session, data):
    return client.post(f"/meetings/manual/{session.pk}/save/", data, content_type="application/json")


def test_manual_retry_acknowledges_normalized_commit_without_incrementing_version(admin_client, manual_request):
    session, data = manual_request
    committed = save_manual(admin_client, session, data).json()
    # The first response was lost; retry with the same original version/content.
    retry = save_manual(admin_client, session, data)
    assert retry.status_code == 200
    assert retry.json()["version"] == 1
    assert retry.json()["updated_at"] == committed["updated_at"]
    assert retry.json()["state"]["title"] == "已保存的修改"
    session.refresh_from_db()
    assert session.version == 1 and session.meeting_note.title == "已保存的修改"


@pytest.mark.parametrize("change", ["different_content", "newer_revision", "newer_same_content", "confirmed"])
def test_manual_retry_cannot_overwrite_a_real_conflict(admin_client, manual_request, change):
    session, data = manual_request
    assert save_manual(admin_client, session, data).status_code == 200
    retry = deepcopy(data)
    if change == "different_content":
        retry["state"]["title"] = "未被确认的新输入"
    elif change in {"newer_revision", "newer_same_content"}:
        state = data["state"] if change == "newer_same_content" else {**data["state"], "title": "另一页的更新"}
        assert save_manual(admin_client, session, {"version": 1, "state": state}).status_code == 200
    else:
        confirm_session(session.pk, 1)
    session.refresh_from_db()
    expected = (session.version, session.state, session.confirmed_at)
    assert save_manual(admin_client, session, retry).status_code == 409
    session.refresh_from_db()
    assert (session.version, session.state, session.confirmed_at) == expected


@pytest.fixture
def review_request(db, admin_client):
    note = MeetingNote.objects.create(title="AI 周会", meeting_date=timezone.localdate(), raw_text="原文", parse_status="success")
    draft = ImportDraft.objects.create(meeting_note=note, payload={"summary": "摘要", "uncertainties": [],
        "tasks": [{"title": "接口开发", "status": "in_progress", "priority": "normal", "progress": 50}],
        "risks": [], "milestones": []})
    page = admin_client.get(f"/drafts/{draft.pk}/")
    return draft, {"review_version": "0", "review_baseline": page.context["review_baseline"],
        "task_0_action": "create", "task_0_title": "已保存的审阅修改", "task_0_status": "not_started",
        "task_0_progress": "0", "task_0_project": "", "task_0_assignee": "", "task_0_next_step": "待补全"}


def save_review(client, draft, data):
    return client.post(f"/drafts/{draft.pk}/save/", data, HTTP_X_REQUESTED_WITH="XMLHttpRequest")


def test_review_retry_acknowledges_commit_without_reinterpreting_original_baseline(admin_client, review_request):
    draft, data = review_request
    committed = save_review(admin_client, draft, data).json()
    draft.refresh_from_db()
    state = deepcopy(draft.review_state)
    retry = save_review(admin_client, draft, {**data, "csrfmiddlewaretoken": "rotated-token"})
    assert retry.status_code == 200
    assert retry.json() == committed
    draft.refresh_from_db()
    assert draft.review_version == 1 and draft.review_state == state
    assert draft.payload["tasks"][0]["title"] == "接口开发"
    assert not Task.objects.exists()


@pytest.mark.parametrize("change", ["different_content", "tampered_baseline", "newer_revision", "newer_same_content", "confirmed", "replaced", "parsing"])
def test_review_retry_cannot_overwrite_a_real_conflict(admin_client, review_request, change):
    draft, data = review_request
    assert save_review(admin_client, draft, data).status_code == 200
    retry = {**data}
    if change == "different_content":
        retry["task_0_title"] = "未被确认的新输入"
    elif change == "tampered_baseline":
        retry["review_baseline"] += "tampered"
    elif change in {"newer_revision", "newer_same_content"}:
        title = data["task_0_title"] if change == "newer_same_content" else "另一页的更新"
        assert save_review(admin_client, draft, {**data, "review_version": "1", "task_0_title": title}).status_code == 200
    elif change == "confirmed":
        draft.confirmed_at = timezone.now()
        draft.save(update_fields=["confirmed_at"])
    elif change == "replaced":
        ImportDraft.objects.create(meeting_note=draft.meeting_note, payload=draft.payload)
    else:
        draft.meeting_note.parse_status = "parsing"
        draft.meeting_note.save(update_fields=["parse_status"])
    draft.refresh_from_db()
    expected = (draft.review_version, deepcopy(draft.review_state), draft.review_saved_at)
    assert save_review(admin_client, draft, retry).status_code == 409
    draft.refresh_from_db()
    assert (draft.review_version, draft.review_state, draft.review_saved_at) == expected


def test_review_native_save_retains_redirect_and_strict_version_check(admin_client, review_request):
    draft, data = review_request
    response = admin_client.post(f"/drafts/{draft.pk}/save/", data)
    assert response.status_code == 302 and response.url == f"/drafts/{draft.pk}/"
    assert admin_client.post(f"/drafts/{draft.pk}/save/", data).status_code == 409
