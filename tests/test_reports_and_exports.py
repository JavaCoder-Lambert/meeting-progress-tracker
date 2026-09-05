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
