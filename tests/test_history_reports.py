from datetime import timedelta

from django.utils import timezone

from core.models import MeetingNote, ProgressUpdate, Project, Risk, Task
from core.services.meeting_corrections import confirm_correction, correction_baseline
from core.services.reports import build_weekly_report
from core.services.risk_followups import confirm_followup, risk_baseline


def test_report_uses_correction_with_audit_link_not_original_wrong_text(admin_client):
    day = timezone.localdate() - timedelta(days=2)
    task = Task.objects.create(project=Project.objects.create(name="仓配"), title="历史工作", progress=90)
    note = MeetingNote.objects.create(title="当日会议", meeting_date=day, raw_text="原纪要保留")
    update = ProgressUpdate.objects.create(task=task, meeting_note=note, occurred_on=day,
        new_progress=20, new_status="in_progress", completed_work="录错的工作")
    confirm_correction(update.pk, reason="与会议录音核对", proposed={"completed_work": "实际完成工作", "new_progress": 40},
        baseline=correction_baseline(update), token="report-correction")
    report = build_weekly_report(day, day)
    assert "实际完成工作" in report.markdown
    assert "录错的工作" not in report.markdown
    page = admin_client.get(f"/reports/weekly/?start={day}&end={day}")
    assert "已更正" in page.content.decode()
    assert f"/meetings/{note.pk}/history/" in page.content.decode()
    task.refresh_from_db()
    assert task.progress == 90


def test_report_separates_new_risks_and_followups_by_business_date(db):
    today = timezone.localdate()
    project = Project.objects.create(name="履约")
    old = MeetingNote.objects.create(title="上周会", meeting_date=today-timedelta(days=7), raw_text="")
    risk = Risk.objects.create(project=project, content="外部接口未给", source_meeting=old)
    today_meeting = MeetingNote.objects.create(title="今天", meeting_date=today, raw_text="")
    Risk.objects.create(project=project, content="本次发现问题", source_meeting=today_meeting)
    confirm_followup(risk.pk, today_meeting, response="已催对方", next_step="下午核对", reason="", status="tracking",
        owner_id=None, due_date="", baseline=risk_baseline(risk), token="report-follow")
    project.name = "改名后的项目"
    project.save()
    report = build_weekly_report(today, today)
    assert "本周新增" in report.markdown
    assert "本周跟进" in report.markdown
    assert "外部接口未给" not in "\n".join(report.risks)
    assert any("【履约】" in line and "已催对方" in line and "下午核对" in line for line in report.followups)
    assert not build_weekly_report(today+timedelta(days=1), today+timedelta(days=1)).followups
