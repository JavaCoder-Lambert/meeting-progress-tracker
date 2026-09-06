from datetime import timedelta

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from core.models import MeetingNote, ParseJob, Person, Project, Task


def meeting_state(**kwargs):
    return {"title": "订单履约周会", "meeting_date": timezone.localdate().isoformat(),
            "meeting_time": "09:30", "agenda": "逐人核对进展", "project_ids": [],
            "person_ids": [], "items": [], **kwargs}


@pytest.mark.django_db
def test_manual_create_page_is_authenticated_and_has_both_entry_modes(admin_client):
    assert Client().get("/meetings/manual/new/").status_code == 302
    response = admin_client.get("/meetings/manual/new/")
    assert response.status_code == 200
    assert "会议" in response.content.decode()
    assert reverse("meeting_create") in response.content.decode()


@pytest.mark.django_db
def test_manual_create_saves_metadata_without_parse_job(admin_client):
    project = Project.objects.create(name="订单履约")
    person = Person.objects.create(name="张川")
    response = admin_client.post("/meetings/manual/new/", {
        "title": "逐项记录", "meeting_date": timezone.localdate().isoformat(),
        "meeting_time": "10:30", "agenda": "检查阻塞", "projects": [project.pk], "people": [person.pk],
    })
    assert response.status_code == 302
    note = MeetingNote.objects.get(title="逐项记录")
    session = note.manual_session
    assert session.state["project_ids"] == [project.pk]
    assert session.state["person_ids"] == [person.pk]
    assert session.state["meeting_time"] == "10:30"
    assert ParseJob.objects.count() == 0
    workspace = admin_client.get(response.url)
    assert workspace.status_code == 200
    assert workspace.context["session_data"]["version"] == 0
    assert admin_client.get(reverse("meeting_detail", args=[note.pk])).url == response.url


@pytest.mark.django_db
def test_manual_create_rejects_invalid_dates_and_preserves_entered_title(admin_client):
    response = admin_client.post("/meetings/manual/new/", {"title": "不能丢失的主题", "meeting_date": "invalid"})
    assert response.status_code == 200
    assert "不能丢失的主题" in response.content.decode()
    assert MeetingNote.objects.count() == 0


@pytest.mark.django_db
def test_manual_reference_requires_authentication(admin_client):
    response = Client().post("/meetings/manual/references/", {"kind": "person", "name": "张川"}, content_type="application/json")
    assert response.status_code == 302
    assert Person.objects.count() == 0
    response = admin_client.post("/meetings/manual/references/", {"kind": "person", "name": "张川"}, content_type="application/json")
    assert response.status_code == 200
    assert response.json()["item"]["name"] == "张川"


@pytest.mark.django_db
def test_manual_save_rejects_stale_or_malformed_requests_and_keeps_server_draft(admin_client):
    from core.services.manual_meetings import create_session
    session = create_session(meeting_state())
    url = reverse("manual_meeting_save", args=[session.pk])
    state = {**session.state, "agenda": "尚未决定归属项目"}
    result = admin_client.post(url, {"version": 0, "state": state}, content_type="application/json")
    assert result.status_code == 200
    assert result.json()["version"] == 1
    assert result.json()["state"]["agenda"] == "尚未决定归属项目"
    for body, expected in [({"version": 0, "state": session.state}, 409), ([], 400)]:
        result = admin_client.post(url, body, content_type="application/json")
        assert result.status_code == expected
    session.refresh_from_db()
    assert session.state["agenda"] == "尚未决定归属项目"
    assert Task.objects.count() == 0


@pytest.mark.django_db
def test_manual_save_requires_csrf_and_rejects_get(admin_user):
    from core.services.manual_meetings import create_session
    session = create_session(meeting_state())
    client = Client(enforce_csrf_checks=True)
    client.force_login(admin_user)
    url = reverse("manual_meeting_save", args=[session.pk])
    assert client.post(url, {"version": 0, "state": session.state}, content_type="application/json").status_code == 403
    assert client.get(url).status_code == 405


@pytest.mark.django_db
def test_manual_pending_list_and_parse_guard(admin_client):
    from core.services.manual_meetings import create_session
    session = create_session(meeting_state())
    inbox = admin_client.get(reverse("meeting_list"), {"state": "pending"})
    assert [row.pk for row in inbox.context["meeting_list"]] == [session.meeting_note_id]
    assert reverse("manual_meeting_workspace", args=[session.pk]) in inbox.content.decode()
    response = admin_client.post(reverse("meeting_parse", args=[session.meeting_note_id]), HTTP_X_REQUESTED_WITH="XMLHttpRequest")
    assert response.status_code == 422
    assert ParseJob.objects.count() == 0


@pytest.mark.django_db
def test_manual_preview_then_confirm_and_repeat_is_once_only(admin_client):
    from uuid import uuid4
    from core.services.manual_meetings import create_session
    from core.models import Risk
    project = Project.objects.create(name="SKU")
    session = create_session(meeting_state(items=[{
        "id": str(uuid4()), "kind": "risk", "project_id": project.pk, "content": "接口字段待确认", "recorded": True,
    }]))
    preview_url = reverse("manual_meeting_preview", args=[session.pk])
    response = admin_client.get(preview_url)
    assert response.status_code == 200
    assert "接口字段待确认" in response.content.decode()
    assert Risk.objects.count() == 0
    confirm_url = reverse("manual_meeting_confirm", args=[session.pk])
    for _ in range(2):
        response = admin_client.post(confirm_url, {"version": session.version})
        assert response.status_code == 302
    assert Risk.objects.count() == 1
    session.refresh_from_db()
    response = admin_client.get(reverse("manual_meeting_workspace", args=[session.pk]))
    assert response.context["session_data"]["confirmed"]
    assert "接口字段待确认" in response.content.decode()
    assert session.meeting_note_id not in [n.pk for n in admin_client.get(reverse("meeting_list"), {"state": "pending"}).context["meeting_list"]]


@pytest.mark.django_db
def test_future_meeting_can_plan_but_cannot_confirm(admin_client):
    from core.services.manual_meetings import create_session
    session = create_session(meeting_state(meeting_date=(timezone.localdate() + timedelta(days=1)).isoformat()))
    response = admin_client.post(reverse("manual_meeting_confirm", args=[session.pk]), {"version": 0})
    assert response.status_code == 400
    session.refresh_from_db()
    assert session.confirmed_at is None


@pytest.mark.django_db
def test_manual_drafts_are_resumable_from_dashboard(admin_client):
    from core.services.manual_meetings import create_session
    session = create_session(meeting_state())
    response = admin_client.get(reverse("dashboard"))
    assert response.context["action_counts"]["pending_drafts"] == 1
    assert reverse("manual_meeting_workspace", args=[session.pk]) in response.content.decode()


@pytest.mark.django_db
def test_inline_reference_reuses_exact_name_and_handles_malformed_kind(admin_client):
    url = reverse("manual_meeting_reference")
    for _ in range(2):
        response = admin_client.post(url, {"kind": "project", "name": " SKU "}, content_type="application/json")
        assert response.status_code == 200
    assert Project.objects.filter(name="SKU").count() == 1
    for body in ({"kind": [], "name": "x"}, {"kind": "person", "name": "x" * 81}, {"kind": "person", "name": []}):
        assert admin_client.post(url, body, content_type="application/json").status_code == 400


@pytest.mark.django_db
def test_task_history_shows_backdated_business_time_and_no_current_change(admin_client):
    from core.models import ProgressUpdate
    project = Project.objects.create(name="SKU")
    task = Task.objects.create(project=project, title="接口")
    ProgressUpdate.objects.create(task=task, occurred_on=timezone.localdate() - timedelta(days=14),
                                  completed_work="之前的会议补记", applied_to_task=False)
    response = admin_client.get(reverse("task_detail", args=[task.pk]))
    html = response.content.decode()
    assert (timezone.localdate() - timedelta(days=14)).isoformat() in html
    assert "仅补记历史" in html


@pytest.mark.django_db
def test_task_history_orders_legacy_early_morning_by_local_business_date(admin_client):
    from datetime import date, datetime
    from zoneinfo import ZoneInfo
    from core.models import ProgressUpdate
    project = Project.objects.create(name="履约")
    task = Task.objects.create(project=project, title="校验日期顺序")
    earlier_business = ProgressUpdate.objects.create(
        task=task, occurred_on=date(2026, 9, 5), completed_work="昨天会议的补记",
        recorded_at=datetime(2026, 9, 7, 10, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    later_business = ProgressUpdate.objects.create(
        task=task, completed_work="今天凌晨直接更新",
        recorded_at=datetime(2026, 9, 6, 1, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    response = admin_client.get(reverse("task_detail", args=[task.pk]))
    assert [row.pk for row in response.context["progress_updates"]] == [later_business.pk, earlier_business.pk]
