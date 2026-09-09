from datetime import date

from core.models import MeetingNote, MeetingSession, ProgressUpdate, Project, Task


def test_meeting_filters_combine_topic_dates_and_preserve_query_in_pages(admin_client):
    for index in range(21):
        MeetingNote.objects.create(title=f"周会履约{index}", meeting_date=date(2026, 9, 4), raw_text="原文")
    MeetingNote.objects.create(title="财务对接", meeting_date=date(2026, 9, 4), raw_text="履约")
    MeetingNote.objects.create(title="周会履约旧记录", meeting_date=date(2026, 8, 20), raw_text="原文")
    page = admin_client.get("/meetings/?q=履约&start=2026-09-01&end=2026-09-05")
    assert page.context["page_obj"].paginator.count == 21
    assert "财务对接" not in page.content.decode()
    assert "旧记录" not in page.content.decode()
    assert "start=2026-09-01" in page.context["querystring"]


def test_project_meetings_use_explicit_links_and_historical_snapshots_not_titles(admin_client):
    project = Project.objects.create(name="仓配")
    other = Project.objects.create(name="财务")
    task = Task.objects.create(project=other, title="已转项目任务")
    historical = MeetingNote.objects.create(title="历史会", meeting_date=date(2026, 9, 4), raw_text="")
    ProgressUpdate.objects.create(task=task, meeting_note=historical, snapshot={"project_id": project.pk})
    manual = MeetingNote.objects.create(title="待开会", meeting_date=date(2026, 9, 4), raw_text="")
    MeetingSession.objects.create(meeting_note=manual, state={"project_ids": [project.pk], "items": []})
    MeetingNote.objects.create(title="仓配：只有名字相似", meeting_date=date(2026, 9, 4), raw_text="")
    page = admin_client.get(f"/meetings/?project={project.pk}")
    assert {note.pk for note in page.context["meeting_list"]} == {historical.pk, manual.pk}
    assert f"/meetings/?project={project.pk}" in admin_client.get(f"/projects/{project.pk}/").content.decode()


def test_invalid_date_filter_is_visible_and_does_not_break_list(admin_client):
    page = admin_client.get("/meetings/?start=2026-02-30")
    assert page.status_code == 200
    assert page.context["filter_error"]
