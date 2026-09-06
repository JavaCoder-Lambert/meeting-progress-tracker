from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from core.models import ProgressUpdate, Project, Task
from core.services.progress_updates import record_task_progress
from core.services.reports import build_weekly_report
from django.utils import timezone


@pytest.mark.django_db
def test_weekly_report_uses_updates_in_selected_range():
    project = Project.objects.create(name="金蝶")
    task = Task.objects.create(project=project, title="接口")
    ProgressUpdate.objects.create(task=task, new_status=Task.Status.DONE, completed_work="接口完成", recorded_at=datetime(2026, 9, 3, 10, tzinfo=ZoneInfo("Asia/Shanghai")))
    report = build_weekly_report(date(2026, 8, 31), date(2026, 9, 6))
    assert "接口完成" in report.markdown
    assert "金蝶" in report.markdown


@pytest.mark.django_db
def test_weekly_report_uses_update_status_snapshot_when_task_status_changes_later():
    project = Project.objects.create(name="金蝶")
    task = Task.objects.create(project=project, title="接口", status=Task.Status.IN_PROGRESS)
    ProgressUpdate.objects.create(
        task=task,
        new_status=Task.Status.IN_PROGRESS,
        recorded_at=datetime(2026, 9, 3, 10, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    task.status = Task.Status.DONE
    task.save()

    report = build_weekly_report(date(2026, 8, 31), date(2026, 9, 6))

    assert "接口（进行中）" in report.markdown
    assert "接口（已完成）" not in report.markdown


@pytest.mark.django_db
def test_progress_update_service_entry_is_in_current_weekly_report():
    project = Project.objects.create(name="仓配升级")
    task = Task.objects.create(project=project, title="完成联调", status=Task.Status.IN_PROGRESS)

    record_task_progress(task, {"completed_work": "已完成接口联调", "next_step": "安排验收"})

    report = build_weekly_report(timezone.localdate(), timezone.localdate())
    assert "仓配升级" in report.markdown
    assert "完成联调：已完成接口联调" in report.markdown
    assert "完成联调：安排验收" in report.markdown


@pytest.mark.django_db
def test_json_export_has_no_credentials(admin_client, settings):
    settings.LLM_API_KEY = "never-export-this"
    Project.objects.create(name="SKU")
    response = admin_client.get("/settings/export/json/")
    assert response.status_code == 200
    assert b"never-export-this" not in response.content
    assert "SKU" in response.content.decode()


@pytest.mark.django_db
def test_csv_export_is_zip(admin_client):
    response = admin_client.get("/settings/export/csv/")
    assert response.status_code == 200
    assert response["Content-Type"] == "application/zip"


@pytest.mark.django_db
def test_weekly_report_page_uses_current_week_by_default(admin_client):
    response = admin_client.get("/reports/weekly/")
    assert response.status_code == 200
    assert "项目周报" in response.content.decode()


@pytest.mark.django_db
def test_weekly_report_page_handles_invalid_dates(admin_client):
    response = admin_client.get("/reports/weekly/", {"start": "not-a-date", "end": "2026-09-04"})
    assert response.status_code == 200
    assert "日期格式无效" in response.content.decode()


@pytest.mark.django_db
def test_backdated_progress_uses_occurrence_date_and_snapshot_not_current_task():
    project = Project.objects.create(name="现在的项目")
    task = Task.objects.create(project=project, title="后来改过的标题")
    ProgressUpdate.objects.create(
        task=task, occurred_on=date(2026, 8, 20), completed_work="核对迁移数据", new_status=Task.Status.IN_PROGRESS,
        snapshot={"title": "历史任务", "project_name": "原项目", "person_name": "张川"},
        recorded_at=datetime(2026, 9, 6, 10, tzinfo=ZoneInfo("Asia/Shanghai")), applied_to_task=False,
    )
    old_week = build_weekly_report(date(2026, 8, 17), date(2026, 8, 23)).markdown
    new_week = build_weekly_report(date(2026, 8, 31), date(2026, 9, 6)).markdown
    assert "原项目" in old_week and "历史任务：核对迁移数据" in old_week
    assert "后来改过的标题" not in old_week
    assert "核对迁移数据" not in new_week


@pytest.mark.django_db
def test_meeting_risks_follow_business_date_while_direct_risks_keep_created_date():
    from core.models import MeetingNote, Risk
    project = Project.objects.create(name="履约")
    note = MeetingNote.objects.create(title="补记", meeting_date=date(2026, 8, 20), raw_text="")
    risk = Risk.objects.create(project=project, source_meeting=note, content="会议中的风险")
    Risk.objects.filter(pk=risk.pk).update(created_at=datetime(2026, 9, 6, tzinfo=ZoneInfo("Asia/Shanghai")))
    direct = Risk.objects.create(project=project, content="当天手工风险")
    Risk.objects.filter(pk=direct.pk).update(created_at=datetime(2026, 9, 6, tzinfo=ZoneInfo("Asia/Shanghai")))
    old_week = build_weekly_report(date(2026, 8, 17), date(2026, 8, 23)).markdown
    new_week = build_weekly_report(date(2026, 8, 31), date(2026, 9, 6)).markdown
    assert "会议中的风险" in old_week and "会议中的风险" not in new_week
    assert "当天手工风险" in new_week and "当天手工风险" not in old_week


@pytest.mark.django_db
def test_json_export_preserves_manual_draft_and_occurrence_fields(admin_client):
    from core.services.manual_meetings import create_session
    session = create_session({"title": "未结束周会", "meeting_date": "2026-09-06", "agenda": "下次继续"})
    result = admin_client.get("/settings/export/json/").json()
    assert result["meetingsession"][0]["state"]["agenda"] == "下次继续"
    assert result["meetingsession"][0]["meeting_note"] == session.meeting_note_id
    assert result["meetingsession"][0]["confirmed_at"] is None


@pytest.mark.django_db
def test_quick_done_without_narrative_still_appears_in_weekly_completed_section():
    project = Project.objects.create(name="SKU")
    task = Task.objects.create(project=project, title="完成链路验收", status=Task.Status.DONE, progress=100)
    ProgressUpdate.objects.create(task=task, new_status=Task.Status.DONE, new_progress=100,
                                  occurred_on=date(2026, 9, 6), snapshot={"title": "完成链路验收", "project_name": "SKU"})
    report = build_weekly_report(date(2026, 9, 6), date(2026, 9, 6)).markdown
    completed_section = report.split("### 本期完成\n", 1)[1].split("### 进行中", 1)[0]
    assert "完成链路验收" in completed_section
