from datetime import date, datetime
from html.parser import HTMLParser
from zoneinfo import ZoneInfo

import pytest

from core.models import ProgressUpdate, Project, Task
from core.services.reports import build_weekly_report


pytestmark = pytest.mark.django_db
SHANGHAI = ZoneInfo("Asia/Shanghai")


class ReportPreview(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.depth = 0
        self.tags = []
        self.text = []
        self.headings = []
        self.details = []
        self.links = []
        self.heading = None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get("id") == "report-preview":
            self.depth = 1
        elif self.depth:
            self.depth += 1
            self.tags.append(tag)
            if tag in {"h2", "h3"}:
                self.heading = []
            if tag == "details":
                self.details.append(attrs)
            if tag == "a":
                self.links.append(attrs.get("href"))

    def handle_endtag(self, tag):
        if self.depth:
            if tag in {"h2", "h3"}:
                self.headings.append("".join(self.heading))
                self.heading = None
            self.depth -= 1

    def handle_data(self, data):
        if self.depth:
            self.text.append(data)
            if self.heading is not None:
                self.heading.append(data)


def test_weekly_report_combines_work_and_keeps_only_the_last_status_and_next_step():
    project = Project.objects.create(name="订单履约")
    task = Task.objects.create(project=project, title="库存联调")
    ProgressUpdate.objects.create(
        task=task, occurred_on=date(2026, 9, 1),
        recorded_at=datetime(2026, 9, 1, 10, tzinfo=SHANGHAI),
        new_status=Task.Status.IN_PROGRESS,
        completed_work="完成字段映射", next_step="开始接口联调",
    )
    ProgressUpdate.objects.create(
        task=task, occurred_on=date(2026, 9, 3),
        recorded_at=datetime(2026, 9, 3, 10, tzinfo=SHANGHAI),
        new_status=Task.Status.ACCEPTANCE,
        completed_work="通过联调用例", next_step="安排业务验收",
    )

    markdown = build_weekly_report(date(2026, 8, 31), date(2026, 9, 6)).markdown

    assert "- 库存联调：完成字段映射；通过联调用例" in markdown
    assert "库存联调（待验收）" in markdown
    assert "库存联调（进行中）" not in markdown
    assert "库存联调：安排业务验收" in markdown
    assert "开始接口联调" not in markdown


@pytest.mark.parametrize("final_status", [Task.Status.DONE, Task.Status.ACCEPTANCE])
def test_late_backfill_cannot_replace_the_period_end_status_or_restore_cleared_next_step(final_status):
    project = Project.objects.create(name="订单履约")
    task = Task.objects.create(project=project, title="库存联调", status=Task.Status.BLOCKED)
    ProgressUpdate.objects.create(
        task=task, occurred_on=date(2026, 9, 2),
        recorded_at=datetime(2026, 9, 8, 10, tzinfo=SHANGHAI),
        new_status=Task.Status.IN_PROGRESS,
        completed_work="补记字段映射", next_step="过时的联调计划",
    )
    ProgressUpdate.objects.create(
        task=task, occurred_on=date(2026, 9, 5),
        recorded_at=datetime(2026, 9, 6, 10, tzinfo=SHANGHAI),
        new_status=Task.Status.ACCEPTANCE, next_step="过时的验收计划",
    )
    ProgressUpdate.objects.create(
        task=task, occurred_on=date(2026, 9, 5),
        recorded_at=datetime(2026, 9, 6, 11, tzinfo=SHANGHAI),
        new_status=Task.Status.ACCEPTANCE, next_step="验收中",
    )
    ProgressUpdate.objects.create(
        task=task, occurred_on=date(2026, 9, 5),
        recorded_at=datetime(2026, 9, 6, 11, tzinfo=SHANGHAI),
        new_status=final_status, completed_work="完成验收", next_step="",
    )

    markdown = build_weekly_report(date(2026, 8, 31), date(2026, 9, 6)).markdown
    ongoing = markdown.split("### 进行中\n", 1)[1].split("### 下一步", 1)[0]
    next_steps = markdown.split("### 下一步\n", 1)[1].split("## 风险", 1)[0]

    assert "库存联调：补记字段映射；完成验收" in markdown
    if final_status == Task.Status.DONE:
        assert "库存联调" not in ongoing
        assert "（已完成）" in markdown
    else:
        assert "库存联调（待验收）" in ongoing
        assert "库存联调（进行中）" not in ongoing
    assert "库存联调" not in next_steps
    assert "过时" not in markdown


def test_report_preview_shows_one_task_summary_with_escaped_text_and_collapsed_history(admin_client):
    project = Project.objects.create(name="后来项目")
    task = Task.objects.create(project=project, title="后来标题", status=Task.Status.DONE)
    title = '<img src=x onerror="alert(1)">历史任务'
    for business_day, values in [
        (1, {"new_status": Task.Status.IN_PROGRESS, "completed_work": "完成字段映射", "next_step": "早期计划"}),
        (3, {"new_status": Task.Status.ACCEPTANCE, "new_progress": 80,
             "completed_work": '<script>alert("进展")</script>', "next_step": "**安排验收**"}),
    ]:
        ProgressUpdate.objects.create(
            task=task, occurred_on=date(2026, 9, business_day),
            snapshot={"title": title, "project_id": project.pk, "project_name": "原项目"},
            **values,
        )

    response = admin_client.get("/reports/weekly/", {"start": "2026-08-31", "end": "2026-09-06"})
    html = response.content.decode()
    preview = ReportPreview(html)
    text = "".join(preview.text)

    assert response.status_code == 200
    assert "pre" not in preview.tags
    assert preview.headings.count(title) == 1
    assert "原项目" in preview.headings
    assert "期末状态" in text and "待验收" in text and "80%" in text
    assert "本期进展" in text and "完成字段映射" in text
    assert '<script>alert("进展")</script>' in text
    assert "下一步" in text and "**安排验收**" in text
    assert "script" not in preview.tags and "img" not in preview.tags
    assert len(preview.details) == 1 and "open" not in preview.details[0]
    assert "早期计划" in text and "2026-09-01" in text
    assert f"/tasks/{task.pk}/" in preview.links
    assert "后来标题" not in text and "后来项目" not in text
    assert 'data-copy-target="report"' in html and 'id="report"' in html


def test_different_tasks_and_projects_with_matching_snapshot_names_remain_separate(admin_client):
    first_project = Project.objects.create(name="后来项目甲")
    second_project = Project.objects.create(name="后来项目乙")
    tasks = [
        Task.objects.create(project=first_project, title="当前甲任务"),
        Task.objects.create(project=first_project, title="当前乙任务"),
        Task.objects.create(project=second_project, title="当前丙任务"),
    ]
    for task, status, work in zip(tasks, [Task.Status.DONE, Task.Status.ACCEPTANCE, Task.Status.BLOCKED],
                                  ["甲工作", "乙工作", "丙工作"], strict=True):
        ProgressUpdate.objects.create(
            task=task, occurred_on=date(2026, 9, 3), new_status=status, completed_work=work,
            snapshot={"project_id": task.project_id, "project_name": "历史同名项目", "title": "同名任务"},
        )

    response = admin_client.get("/reports/weekly/", {"start": "2026-08-31", "end": "2026-09-06"})
    preview = ReportPreview(response.content.decode())

    assert preview.headings.count("历史同名项目") == 2
    assert preview.headings.count("同名任务") == 3
    assert preview.links == [f"/tasks/{task.pk}/" for task in tasks]
    assert "甲工作" in response.context["markdown"]
    assert "乙工作" in response.context["markdown"]
    assert "丙工作" in response.context["markdown"]


def test_updates_without_occurrence_date_use_the_shanghai_day_for_range_and_latest():
    project = Project.objects.create(name="库存")
    task = Task.objects.create(project=project, title="接口")
    ProgressUpdate.objects.create(
        task=task, recorded_at=datetime(2026, 9, 5, 16, 30, tzinfo=ZoneInfo("UTC")),
        new_status=Task.Status.ACCEPTANCE, completed_work="凌晨联调完成", next_step="当天验收",
    )
    ProgressUpdate.objects.create(
        task=task, occurred_on=date(2026, 9, 5),
        recorded_at=datetime(2026, 9, 7, 10, tzinfo=SHANGHAI),
        new_status=Task.Status.BLOCKED, next_step="旧阻塞处理",
    )
    ProgressUpdate.objects.create(
        task=task, recorded_at=datetime(2026, 9, 6, 16, 30, tzinfo=ZoneInfo("UTC")),
        new_status=Task.Status.DONE, completed_work="次周工作不能进入本周",
    )

    markdown = build_weekly_report(date(2026, 9, 5), date(2026, 9, 6)).markdown

    assert "接口：凌晨联调完成" in markdown
    assert "接口（待验收）" in markdown
    assert "接口：当天验收" in markdown
    assert "旧阻塞处理" not in markdown
    assert "次周工作不能进入本周" not in markdown


def test_last_snapshot_supplies_missing_status_and_progress_without_reading_current_task(admin_client):
    project = Project.objects.create(name="当前项目")
    task = Task.objects.create(project=project, title="当前任务", status=Task.Status.DONE, progress=100)
    ProgressUpdate.objects.create(
        task=task, occurred_on=date(2026, 9, 3), completed_work="确认需求边界",
        snapshot={"project_id": project.pk, "project_name": "当时项目", "title": "当时任务",
                  "status": Task.Status.IN_PROGRESS, "progress": 30},
    )

    response = admin_client.get("/reports/weekly/", {"start": "2026-08-31", "end": "2026-09-06"})
    preview = ReportPreview(response.content.decode())
    text = "".join(preview.text)

    assert "当时任务（进行中）" in response.context["markdown"]
    assert "30%" in text
    assert "已完成" not in text and "100%" not in text
    assert "当前项目" not in text and "当前任务" not in text


def test_project_renamed_during_the_period_stays_together_under_its_last_snapshot_name(admin_client):
    project = Project.objects.create(name="报告期间后再次改名")
    early_task = Task.objects.create(project=project, title="接口")
    later_task = Task.objects.create(project=project, title="验收")
    ProgressUpdate.objects.create(
        task=early_task, occurred_on=date(2026, 9, 1), new_status=Task.Status.DONE,
        snapshot={"project_id": project.pk, "project_name": "期初名称", "title": "接口"},
    )
    ProgressUpdate.objects.create(
        task=later_task, occurred_on=date(2026, 9, 3), new_status=Task.Status.ACCEPTANCE,
        snapshot={"project_id": project.pk, "project_name": "期末名称", "title": "验收"},
    )

    response = admin_client.get("/reports/weekly/", {"start": "2026-08-31", "end": "2026-09-06"})
    preview = ReportPreview(response.content.decode())

    assert preview.headings.count("期末名称") == 1
    assert "期初名称" not in preview.headings
    assert "报告期间后再次改名" not in preview.headings
    assert "接口" in preview.headings and "验收" in preview.headings
    assert "## 期初名称" not in response.context["markdown"]
