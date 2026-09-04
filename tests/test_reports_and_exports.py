from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from core.models import ProgressUpdate, Project, Task
from core.services.reports import build_weekly_report


@pytest.mark.django_db
def test_weekly_report_uses_updates_in_selected_range():
    project = Project.objects.create(name="金蝶")
    task = Task.objects.create(project=project, title="接口")
    ProgressUpdate.objects.create(task=task, new_status=Task.Status.DONE, completed_work="接口完成", recorded_at=datetime(2026, 9, 3, 10, tzinfo=ZoneInfo("Asia/Shanghai")))
    report = build_weekly_report(date(2026, 8, 31), date(2026, 9, 6))
    assert "接口完成" in report.markdown
    assert "金蝶" in report.markdown


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
