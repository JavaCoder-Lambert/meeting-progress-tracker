from datetime import date

import pytest
from django.urls import reverse
from django.utils import timezone

from core.models import Person, ProgressUpdate, Project, ProjectPhase, Task
from core.services.progress_updates import record_task_progress
from core.services.reports import build_weekly_report


pytestmark = pytest.mark.django_db


@pytest.fixture
def task():
    project = Project.objects.create(name="订单履约")
    phase = ProjectPhase.objects.create(project=project, name="接口联调")
    person = Person.objects.create(name="小李")
    return Task.objects.create(project=project, phase=phase, assignee=person,
                               title="同步库存", status="in_progress", progress=10,
                               due_date=date(2026, 9, 20))


def page_payload(client, url, form_name="form"):
    response = client.get(url)
    form = response.context[form_name]
    return {name: "" if form[name].value() is None else form[name].value() for name in form.fields}


def test_old_full_edit_cannot_overwrite_new_progress_and_keeps_user_input(admin_client, task):
    url = reverse("task_edit", args=[task.pk])
    payload = page_payload(admin_client, url)
    record_task_progress(task, {"progress": 60, "current_note": "会议已确认联调完成"})

    response = admin_client.post(url, {**payload, "current_note": "旧页上新写的说明"})

    task.refresh_from_db()
    assert response.status_code == 409
    assert (task.progress, task.current_note) == (60, "会议已确认联调完成")
    assert response.context["form"]["current_note"].value() == "旧页上新写的说明"
    assert "重新核对" in response.content.decode()
    assert "会议已确认联调完成" in response.content.decode()
    assert ProgressUpdate.objects.count() == 1


def test_scheduling_without_timestamp_change_invalidates_open_edit(admin_client, task):
    url = reverse("task_edit", args=[task.pk])
    payload = page_payload(admin_client, url)
    before = task.updated_at
    schedule_page = admin_client.get(reverse("task_detail", args=[task.pk]))
    response = admin_client.post(reverse("task_schedule", args=[task.pk]), {
        "action": "date", "planned_for": "2026-09-12",
        "task_baseline": schedule_page.context.get("task_baseline", ""),
    })
    assert response.status_code == 302
    task.refresh_from_db()
    assert task.updated_at == before

    response = admin_client.post(url, {**payload, "current_note": "不能清空别处刚安排的日期"})

    task.refresh_from_db()
    assert response.status_code == 409
    assert task.planned_for == date(2026, 9, 12)
    assert task.current_note == ""


@pytest.mark.parametrize("route,form_name", [("task_edit", "form"), ("task_detail", "progress_form")])
@pytest.mark.parametrize("token", ["", "forged-task-state"])
def test_missing_or_forged_baseline_never_changes_task(admin_client, task, route, form_name, token):
    payload = page_payload(admin_client, reverse(route, args=[task.pk]), form_name)
    destination = "task_progress_update" if route == "task_detail" else route
    response = admin_client.post(reverse(destination, args=[task.pk]), {
        **payload, "task_baseline": token, "progress": "70", "current_note": "保留输入",
    })
    task.refresh_from_db()
    assert response.status_code in (200, 409)
    assert task.progress == 10
    assert task.current_note == ""
    assert not task.progress_updates.exists()
    assert response.context[form_name]["current_note"].value() == "保留输入"
    assert "基线" in response.content.decode()


def test_old_progress_form_keeps_newer_task_and_submitted_notes(admin_client, task):
    payload = page_payload(admin_client, reverse("task_detail", args=[task.pk]), "progress_form")
    record_task_progress(task, {"progress": 60})
    response = admin_client.post(reverse("task_progress_update", args=[task.pk]), {
        **payload, "completed_work": "旧页补充的本次完成", "next_step": "准备验收",
    })
    task.refresh_from_db()
    assert response.status_code == 409
    assert task.progress == 60
    assert response.context["progress_form"]["completed_work"].value() == "旧页补充的本次完成"
    assert "准备验收" in response.content.decode()
    assert task.progress_updates.count() == 1


def test_explicit_recheck_refreshes_baseline_without_saving_then_allows_reviewed_edit(admin_client, task):
    url = reverse("task_edit", args=[task.pk])
    payload = page_payload(admin_client, url)
    record_task_progress(task, {"progress": 60})
    payload["current_note"] = "准备验收"
    conflict = admin_client.post(url, payload)
    assert conflict.status_code == 409

    reviewed = admin_client.post(url, {**payload, "intent": "review_current"})
    task.refresh_from_db()
    assert reviewed.status_code == 200
    assert task.progress == 60
    assert task.current_note == ""
    form = reviewed.context["form"]
    assert form["current_note"].value() == "准备验收"
    assert form["task_baseline"].value() != payload["task_baseline"]
    assert "60%" in reviewed.content.decode()

    saved = admin_client.post(url, {**payload, "progress": "60", "task_baseline": form["task_baseline"].value()})
    task.refresh_from_db()
    assert saved.status_code == 302
    assert (task.progress, task.current_note) == (60, "准备验收")


@pytest.mark.parametrize("destination,form_name", [("task_edit", "form"), ("task_progress_update", "progress_form")])
def test_another_tasks_signed_baseline_cannot_authorize_edit_or_recheck(admin_client, task, destination, form_name):
    other = Task.objects.create(project=task.project, title="另外一个任务")
    source = "task_detail" if destination == "task_progress_update" else destination
    payload = page_payload(admin_client, reverse(source, args=[task.pk]), form_name)
    other_data = page_payload(admin_client, reverse(source, args=[other.pk]), form_name)
    for intent in ("", "review_current"):
        response = admin_client.post(reverse(destination, args=[task.pk]), {
            **payload, "task_baseline": other_data["task_baseline"], "intent": intent, "progress": "70",
        })
        assert response.status_code == 409
        task.refresh_from_db()
        assert task.progress == 10
        assert not task.progress_updates.exists()


def test_progress_recheck_preserves_text_and_requires_separate_confirmation(admin_client, task):
    payload = page_payload(admin_client, reverse("task_detail", args=[task.pk]), "progress_form")
    record_task_progress(task, {"progress": 60})
    payload["completed_work"] = "待核对的文字"
    reviewed = admin_client.post(reverse("task_progress_update", args=[task.pk]), {**payload, "intent": "review_current"})
    task.refresh_from_db()
    assert reviewed.status_code == 200
    assert task.progress == 60
    assert task.progress_updates.count() == 1
    form = reviewed.context["progress_form"]
    assert form["completed_work"].value() == "待核对的文字"
    assert form["task_baseline"].value() != payload["task_baseline"]
    saved = admin_client.post(reverse("task_progress_update", args=[task.pk]), {
        **payload, "task_baseline": form["task_baseline"].value(), "progress": "60",
    })
    assert saved.status_code == 302
    assert task.progress_updates.count() == 2


def test_progress_recheck_with_invalid_date_keeps_current_data_and_confirmation_visible(admin_client, task):
    payload = page_payload(admin_client, reverse("task_detail", args=[task.pk]), "progress_form")
    record_task_progress(task, {"progress": 60, "current_note": "会议已更新到六成"})
    destination = f"/projects/{task.project_id}/?show_done=1"
    reviewed = admin_client.post(reverse("task_progress_update", args=[task.pk]), {
        **payload, "intent": "review_current", "due_date": "invalid-date",
        "completed_work": "保留本次填写", "next": destination,
    })
    task.refresh_from_db()
    assert reviewed.status_code == 200
    assert task.progress == 60
    assert task.progress_updates.count() == 1
    form = reviewed.context["progress_form"]
    assert form["due_date"].errors
    assert form["task_baseline"].value() != payload["task_baseline"]
    assert form["completed_work"].value() == "保留本次填写"
    assert reviewed.context["return_url"] == destination
    assert reviewed.context["reviewing"] is True
    content = reviewed.content.decode()
    assert "会议已更新到六成" in content
    assert "确认核对并保存进展" in content


def test_another_change_after_recheck_requires_another_review(admin_client, task):
    url = reverse("task_edit", args=[task.pk])
    payload = page_payload(admin_client, url)
    record_task_progress(task, {"progress": 60})
    reviewed = admin_client.post(url, {**payload, "intent": "review_current"})
    record_task_progress(task, {"progress": 80})
    response = admin_client.post(url, {
        **payload, "task_baseline": reviewed.context["form"]["task_baseline"].value(), "progress": "60",
    })
    task.refresh_from_db()
    assert response.status_code == 409
    assert task.progress == 80


@pytest.mark.parametrize("next_url", ["https://evil.test/", "//evil.test/", "/\\evil.test/"])
def test_edit_rejects_external_return_targets(admin_client, task, next_url):
    url = reverse("task_edit", args=[task.pk])
    payload = page_payload(admin_client, url)
    response = admin_client.post(url, {**payload, "next": next_url, "current_note": "说明"})
    assert response.status_code == 302
    assert response.url == reverse("task_detail", args=[task.pk])


def test_edit_returns_to_original_project_and_query(admin_client, task):
    url = reverse("task_edit", args=[task.pk])
    destination = f"/projects/{task.project_id}/?status=in_progress&q=%E5%BA%93%E5%AD%98"
    payload = page_payload(admin_client, url + "?next=" + destination.replace("&", "%26"))
    response = admin_client.post(url, {**payload, "next": destination, "current_note": "说明"})
    assert response.status_code == 302
    assert response.url == destination


def test_progress_keeps_source_page_through_validation_and_success(admin_client, task):
    destination = f"/projects/{task.project_id}/?status=blocked"
    payload = page_payload(admin_client, reverse("task_detail", args=[task.pk]), "progress_form")
    payload["next"] = destination
    invalid = admin_client.post(reverse("task_progress_update", args=[task.pk]), {**payload, "progress": "101"})
    assert invalid.context["return_url"] == destination
    saved = admin_client.post(reverse("task_progress_update", args=[task.pk]), {**payload, "progress": "60"})
    assert saved.status_code == 302
    detail = admin_client.get(saved.url)
    assert detail.context["return_url"] == destination


def test_task_form_uses_chinese_labels_and_limits_phase_choices_to_selected_project(admin_client, task):
    other = Project.objects.create(name="结算")
    other_phase = ProjectPhase.objects.create(project=other, name="付款核销")
    response = admin_client.get(reverse("task_edit", args=[task.pk]))
    form = response.context["form"]
    assert form["project"].label == "所属项目"
    assert form["assignee"].label == "负责人"
    assert list(form.fields["phase"].queryset) == [task.phase]
    content = response.content.decode()
    assert "更多日期与说明" in content
    assert f'"id": {other_phase.pk}' in content  # The next project can be selected without a request.


def test_bound_phase_and_hidden_errors_are_visible_and_do_not_move_task(admin_client, task):
    other = Project.objects.create(name="结算")
    payload = page_payload(admin_client, reverse("task_edit", args=[task.pk]))
    response = admin_client.post(reverse("task_edit", args=[task.pk]), {
        **payload, "project": other.pk, "planned_start_date": "invalid",
    })
    task.refresh_from_db()
    assert response.status_code == 200
    assert task.project_id != other.pk
    assert response.context["form"]["phase"].errors
    assert response.context["advanced_open"]


@pytest.mark.parametrize("entry", ["progress", "edit"])
def test_new_progress_snapshot_keeps_old_weekly_report_titles_after_task_rename(admin_client, task, entry):
    if entry == "progress":
        payload = page_payload(admin_client, reverse("task_detail", args=[task.pk]), "progress_form")
        response = admin_client.post(reverse("task_progress_update", args=[task.pk]), {
            **payload, "completed_work": "完成接口联调", "progress": "60",
        })
    else:
        payload = page_payload(admin_client, reverse("task_edit", args=[task.pk]))
        response = admin_client.post(reverse("task_edit", args=[task.pk]), {**payload, "progress": "60"})
    assert response.status_code == 302
    snapshot = task.progress_updates.get().snapshot
    assert snapshot["title"] == "同步库存"
    assert snapshot["project_name"] == "订单履约"
    assert snapshot["person_name"] == "小李"
    assert snapshot["due_date"] == "2026-09-20"
    assert snapshot["phase_id"] == task.phase_id
    Task.objects.filter(pk=task.pk).update(title="重新命名的任务")
    Project.objects.filter(pk=task.project_id).update(name="重新命名的项目")
    report = build_weekly_report(timezone.localdate(), timezone.localdate()).markdown
    assert "同步库存" in report
    assert "订单履约" in report
    assert "重新命名" not in report
