from datetime import date

import pytest

from core.models import Project, Task


@pytest.fixture(autouse=True)
def stable_business_today(monkeypatch):
    from django.utils import timezone
    original = timezone.localdate
    monkeypatch.setattr(timezone, "localdate", lambda value=None, timezone=None: date(2026, 9, 20) if value is None else original(value, timezone))


@pytest.mark.django_db
def test_publish_freezes_task_and_repeated_publish_is_safe():
    from core.services.weekplans import create_draft, publish_plan, save_draft
    project = Project.objects.create(name="项目")
    task = Task.objects.create(project=project, title="原名称", progress=25)
    plan = create_draft(project, date(2027, 1, 1))
    assert plan.week_start == date(2026, 12, 28)
    plan = save_draft(plan.pk, 0, {task.pk: "交付说明"})
    plan = publish_plan(plan.pk, plan.version)
    Task.objects.filter(pk=task.pk).update(title="新名称", progress=100)
    assert plan.items.get().snapshot["title"] == "原名称"
    assert publish_plan(plan.pk, 0).pk == plan.pk


@pytest.mark.django_db
def test_revision_carry_and_history_do_not_use_current_completion():
    from core.services.weekplans import create_draft, publish_plan, save_draft, copy_plan, review_rows
    project = Project.objects.create(name="项目")
    task = Task.objects.create(project=project, title="任务", planned_for=date(2026, 9, 7))
    plan = save_draft(create_draft(project, date(2026, 9, 7)).pk, 0, {task.pk: "交付"})
    plan = publish_plan(plan.pk, plan.version)
    Task.objects.filter(pk=task.pk).update(status="done", progress=100)
    assert review_rows(plan)[0]["actual"] is None
    revision = copy_plan(plan.pk, "修订范围")
    assert revision.revision == 2
    assert revision.items.get().delivery == "交付"
    carried = copy_plan(plan.pk, "", carry=True)
    assert carried.week_start == date(2026, 9, 14)
    assert carried.items.count() == 1
    task.refresh_from_db()
    assert task.planned_for == date(2026, 9, 7)


@pytest.mark.django_db
def test_review_uses_confirmed_correction_without_rewriting_original():
    from core.models import ProgressUpdate, MeetingNote
    from core.services.ai_history import task_state
    from core.services.meeting_corrections import confirm_correction, correction_baseline
    from core.services.weekplans import create_draft, save_draft, publish_plan, review_rows
    project = Project.objects.create(name="更正项目")
    task = Task.objects.create(project=project, title="任务", status="in_progress", progress=30)
    plan = save_draft(create_draft(project, date(2026, 9, 7)).pk, 0, {task.pk: "交付"})
    plan = publish_plan(plan.pk, plan.version)
    meeting = MeetingNote.objects.create(title="会议", meeting_date=date(2026, 9, 10))
    update = ProgressUpdate.objects.create(task=task, meeting_note=meeting, occurred_on=meeting.meeting_date, snapshot=task_state(task), new_progress=30, new_status="in_progress")
    confirm_correction(update.pk, reason="原进度记错", proposed={"new_progress": 100, "new_status": "done"}, baseline=correction_baseline(update), token="weekplan-correction")
    assert review_rows(plan)[0]["done"] is True
    update.refresh_from_db()
    task.refresh_from_db()
    assert update.new_progress == task.progress == 30


@pytest.mark.django_db
def test_pages_save_publish_and_reject_stale_draft(admin_client):
    from django.urls import reverse
    project = Project.objects.create(name="页面项目")
    task = Task.objects.create(project=project, title="页面任务")
    url = reverse("weekplan_list", args=[project.pk])
    response = admin_client.post(url, {"week": "2026-09-07"})
    assert response.status_code == 302
    detail = response.url
    assert admin_client.get(detail).status_code == 200
    assert admin_client.post(detail, {"action": "save", "version": 0, "tasks": [task.pk], f"delivery_{task.pk}": "上线"}).status_code == 302
    assert admin_client.post(detail, {"action": "save", "version": 0, "tasks": [task.pk]}).status_code == 409
    assert admin_client.post(detail, {"action": "publish", "version": 1, "tasks": [task.pk], f"delivery_{task.pk}": "上线"}).status_code == 302
    assert "历史信息不足" in admin_client.get(detail).content.decode()


@pytest.mark.django_db
def test_empty_foreign_and_published_edits_are_rejected():
    from django.core.exceptions import ValidationError
    from core.services.weekplans import create_draft, save_draft, publish_plan, copy_plan
    project = Project.objects.create(name="项目")
    other = Project.objects.create(name="其他项目")
    foreign = Task.objects.create(project=other, title="外部任务")
    own = Task.objects.create(project=project, title="内部任务")
    plan = create_draft(project, date(2026, 9, 7))
    with pytest.raises(ValidationError, match="空计划"):
        publish_plan(plan.pk, 0)
    with pytest.raises(ValidationError, match="当前项目"):
        save_draft(plan.pk, 0, {foreign.pk: "错误"})
    plan = save_draft(plan.pk, 0, {own.pk: "正确"})
    publish_plan(plan.pk, plan.version)
    with pytest.raises(ValidationError, match="不可修改"):
        save_draft(plan.pk, plan.version, {})
    with pytest.raises(ValidationError, match="原因"):
        copy_plan(plan.pk, "  ")


@pytest.mark.django_db
def test_business_date_snapshot_and_legacy_gap():
    from core.models import ProgressUpdate, MeetingNote
    from core.services.ai_history import task_state
    from core.services.weekplans import create_draft, save_draft, publish_plan, review_rows
    project = Project.objects.create(name="项目")
    task = Task.objects.create(project=project, title="原名", status="done", progress=100)
    plan = save_draft(create_draft(project, date(2026, 9, 7)).pk, 0, {task.pk: "交付"})
    plan = publish_plan(plan.pk, plan.version)
    historical = MeetingNote.objects.create(title="补录会议", meeting_date=date(2026, 9, 10))
    ProgressUpdate.objects.create(task=task, meeting_note=historical, snapshot=task_state(task))
    ProgressUpdate.objects.create(task=task, occurred_on=date(2026, 9, 14), snapshot={})
    assert review_rows(plan)[0]["done"] is True
    ProgressUpdate.objects.create(task=task, occurred_on=date(2026, 9, 13), snapshot={})
    assert review_rows(plan)[0]["actual"] is None


@pytest.mark.django_db
def test_list_paginates_and_actions_require_login(admin_client, client):
    from django.urls import reverse
    from core.weekplan_models import ProjectWeekPlan
    from datetime import timedelta
    project = Project.objects.create(name="项目")
    ProjectWeekPlan.objects.bulk_create([ProjectWeekPlan(project=project, week_start=date(2026, 1, 5) + timedelta(weeks=i)) for i in range(26)])
    url = reverse("weekplan_list", args=[project.pk])
    assert len(admin_client.get(url).context["page_obj"]) == 25
    assert len(admin_client.get(url + "?page=2").context["page_obj"]) == 1
    assert client.post(url, {"week": "2026-09-07"}).status_code == 302


@pytest.mark.django_db
def test_snapshot_with_missing_values_is_not_complete_history():
    from core.models import ProgressUpdate
    from core.services.weekplans import create_draft, save_draft, publish_plan, review_rows
    project = Project.objects.create(name="项目")
    task = Task.objects.create(project=project, title="任务")
    plan = save_draft(create_draft(project, date(2026, 9, 7)).pk, 0, {task.pk: "交付"})
    plan = publish_plan(plan.pk, plan.version)
    ProgressUpdate.objects.create(task=task, occurred_on=date(2026, 9, 10), snapshot={"title": "任务", "person_name": "", "status": "done", "progress": None, "due_date": None, "planned_for": None})
    assert review_rows(plan)[0]["actual"] is None


@pytest.mark.django_db
def test_conflict_preserves_input_and_requires_explicit_recheck(admin_client):
    from django.urls import reverse
    from core.services.weekplans import create_draft, save_draft
    project = Project.objects.create(name="冲突项目")
    first = Task.objects.create(project=project, title="原任务")
    second = Task.objects.create(project=project, title="我的任务")
    plan = save_draft(create_draft(project, date(2026, 9, 7)).pk, 0, {first.pk: "他人修改"})
    url = reverse("weekplan_detail", args=[plan.pk])
    data = {"action": "save", "version": "0", "tasks": [second.pk], f"delivery_{second.pk}": "保留输入"}
    response = admin_client.post(url, data)
    assert response.status_code == 409
    selected = [choice for choice in response.context["choices"] if choice["selected"]]
    assert selected[0]["task"].pk == second.pk
    assert selected[0]["delivery"] == "保留输入"
    assert str(response.context["form_version"]) == "0"
    assert "他人修改" in response.content.decode()
    assert admin_client.post(url, data).status_code == 409
    response = admin_client.post(url, dict(data, action="recheck", reviewed_version=plan.version))
    assert response.status_code == 200
    assert response.context["form_version"] == plan.version
    assert admin_client.post(url, dict(data, version=plan.version)).status_code == 302


@pytest.mark.django_db
@pytest.mark.parametrize("field", ["content", "current_note"])
def test_review_normalizes_history_notes(field):
    from core.models import ProgressUpdate
    from core.services.ai_history import task_state
    from core.services.weekplans import create_draft, save_draft, publish_plan, review_rows
    project = Project.objects.create(name="说明项目")
    task = Task.objects.create(project=project, title="任务")
    plan = save_draft(create_draft(project, date(2026, 9, 7)).pk, 0, {task.pk: "交付"})
    plan = publish_plan(plan.pk, plan.version)
    snapshot = task_state(task)
    snapshot.pop("current_note", None)
    snapshot[field] = "等待供应商资料"
    ProgressUpdate.objects.create(task=task, occurred_on=date(2026, 9, 10), snapshot=snapshot)
    assert review_rows(plan)[0]["actual"]["note"] == "等待供应商资料"


@pytest.mark.django_db
def test_publish_applies_visible_edits_atomically(admin_client):
    from django.urls import reverse
    from core.services.weekplans import create_draft, save_draft
    project = Project.objects.create(name="发布项目")
    first = Task.objects.create(project=project, title="旧任务")
    second = Task.objects.create(project=project, title="本页新选任务")
    plan = save_draft(create_draft(project, date(2026, 9, 7)).pk, 0, {first.pk: "旧说明"})
    url = reverse("weekplan_detail", args=[plan.pk])
    data = {"action": "publish", "version": plan.version, "tasks": [second.pk], f"delivery_{second.pk}": "本页说明"}
    assert admin_client.post(url, data).status_code == 302
    plan.refresh_from_db()
    assert plan.status == "published"
    assert plan.items.get().task_id == second.pk
    assert plan.items.get().delivery == "本页说明"
    assert admin_client.post(url, data).status_code == 302


@pytest.mark.django_db
def test_unfinished_and_future_weeks_have_honest_cutoffs(admin_client, monkeypatch):
    from django.urls import reverse
    from django.utils import timezone
    from core.models import ProgressUpdate
    from core.services.ai_history import task_state
    from core.services.weekplans import create_draft, save_draft, publish_plan, review_rows
    monkeypatch.setattr(timezone, "localdate", lambda *args: date(2026, 9, 9))
    project = Project.objects.create(name="日期项目")
    task = Task.objects.create(project=project, title="任务", progress=30)
    plan = save_draft(create_draft(project, date(2026, 9, 7)).pk, 0, {task.pk: "交付"})
    plan = publish_plan(plan.pk, plan.version)
    ProgressUpdate.objects.create(task=task, occurred_on=date(2026, 9, 8), snapshot=task_state(task))
    future_snapshot = dict(task_state(task), status="done", progress=100)
    ProgressUpdate.objects.create(task=task, occurred_on=date(2026, 9, 13), snapshot=future_snapshot)
    row = review_rows(plan)[0]
    assert row["actual"]["progress"] == 30
    assert row["actual"]["occurred_on"] == date(2026, 9, 8)
    html = admin_client.get(reverse("weekplan_detail", args=[plan.pk])).content.decode()
    assert "截至今天的进展（本周尚未结束）" in html
    future = save_draft(create_draft(project, date(2026, 9, 14)).pk, 0, {task.pk: "交付"})
    future = publish_plan(future.pk, future.version)
    assert review_rows(future)[0]["actual"] is None
    monkeypatch.setattr(timezone, "localdate", lambda *args: date(2026, 9, 13))
    assert "截至今天的进展（本周尚未结束）" in admin_client.get(reverse("weekplan_detail", args=[plan.pk])).content.decode()
    monkeypatch.setattr(timezone, "localdate", lambda *args: date(2026, 9, 14))
    assert review_rows(plan)[0]["actual"]["progress"] == 100
    assert review_rows(plan)[0]["actual"]["occurred_on"] == date(2026, 9, 13)
    ended = admin_client.get(reverse("weekplan_detail", args=[plan.pk]))
    assert ended.context["review_title"] == "周末实际"
    assert ended.context["cutoff"] == date(2026, 9, 13)


@pytest.mark.django_db
def test_concurrent_publish_only_accepts_exact_retry(admin_client):
    from django.urls import reverse
    from core.services.weekplans import create_draft, save_draft
    project = Project.objects.create(name="并发发布")
    task = Task.objects.create(project=project, title="任务")
    plan = save_draft(create_draft(project, date(2026, 9, 7)).pk, 0, {task.pk: "草稿"})
    url = reverse("weekplan_detail", args=[plan.pk])
    first = {"action": "publish", "version": plan.version, "tasks": [task.pk], f"delivery_{task.pk}": "甲的说明"}
    assert admin_client.post(url, first).status_code == 302
    second = dict(first, **{f"delivery_{task.pk}": "乙的未保存说明"})
    rejected = admin_client.post(url, second)
    assert rejected.status_code == 409
    assert "乙的未保存说明" in rejected.content.decode()
    assert plan.items.get().delivery == "甲的说明"
    assert admin_client.post(url, first).status_code == 302
    assert admin_client.post(url, dict(first, **{f"delivery_{task.pk}": "  甲的说明  "})).status_code == 302
    assert admin_client.post(url, dict(first, tasks=[])).status_code == 409
    assert admin_client.post(url, dict(first, version=0)).status_code == 409
