from datetime import timedelta
import pytest
from django.utils import timezone
from core.models import Project, Task, MeetingNote, ProgressUpdate

@pytest.mark.django_db
def test_historical_correction_preserves_original_and_current():
    from core.services.meeting_corrections import correction_baseline, confirm_correction, effective_progress_updates
    task = Task.objects.create(project=Project.objects.create(name="P"), title="T", progress=80)
    note = MeetingNote.objects.create(title="M", meeting_date=timezone.localdate()-timedelta(days=5), raw_text="原纪要")
    update = ProgressUpdate.objects.create(task=task, meeting_note=note, new_progress=20, new_status="in_progress", occurred_on=note.meeting_date, snapshot={"title":"旧标题","progress":20})
    correction = confirm_correction(update.pk, reason="录错", proposed={"new_progress":30}, baseline=correction_baseline(update), token="first", apply_current=False)
    assert correction.pk
    update.refresh_from_db(); task.refresh_from_db(); note.refresh_from_db()
    assert update.new_progress == 20 and task.progress == 80 and note.raw_text == "原纪要"
    effective = effective_progress_updates(ProgressUpdate.objects.all())[0]
    assert effective.new_progress == 30 and effective.snapshot["progress"] == 30
    assert effective.pk == update.pk and effective.occurred_on == note.meeting_date

@pytest.mark.django_db
def test_risk_followup_historical_and_close_reopen():
    from core.models import Risk
    from core.services.risk_followups import risk_baseline, confirm_followup
    risk = Risk.objects.create(project=Project.objects.create(name="P"), content="待答复")
    note = MeetingNote.objects.create(title="会", meeting_date=timezone.localdate(), raw_text="")
    first = confirm_followup(risk.pk, note, response="已解决", next_step="", reason="完成核实", status="closed", owner_id=None, due_date="", baseline=risk_baseline(risk), token="close")
    risk.refresh_from_db(); assert risk.status == "closed" and first.before["status"] == "open"
    second = confirm_followup(risk.pk, note, response="再次阻塞", next_step="重查", reason="再次失败", status="open", owner_id=None, due_date="", baseline=risk_baseline(risk), token="reopen")
    risk.refresh_from_db(); assert risk.status == "open" and second.before["status"] == "closed"
    old = MeetingNote.objects.create(title="补录", meeting_date=timezone.localdate()-timedelta(days=7), raw_text="")
    follow = confirm_followup(risk.pk, old, response="当时结束", next_step="", reason="补录", status="closed", owner_id=None, due_date="", baseline=risk_baseline(risk), token="old")
    risk.refresh_from_db(); assert risk.status == "open" and not follow.applied_to_risk

@pytest.mark.django_db
def test_manual_risk_draft_does_not_apply_until_meeting_confirmation():
    from core.models import Risk
    from core.services.risk_followups import risk_baseline
    from core.services.manual_meetings import create_session, confirm_session, preview_session
    risk = Risk.objects.create(project=Project.objects.create(name="P"), content="待答复")
    state = {"title":"会", "meeting_date":timezone.localdate().isoformat(), "items":[], "risk_followups":[{
        "risk_id":risk.pk, "response":"拿到答复", "next_step":"实施", "reason":"", "status":"tracking", "owner_id":None,
        "due_date":"", "baseline":risk_baseline(risk), "token":"draft-follow"}]}
    session = create_session(state)
    risk.refresh_from_db(); assert risk.status == "open"
    assert "拿到答复" in preview_session(session)["minutes"]
    confirm_session(session.pk, 0)
    risk.refresh_from_db(); assert risk.status == "tracking" and risk.followups.count() == 1

@pytest.mark.django_db
def test_correction_current_idempotency_stale_baseline_and_later_updates(django_assert_num_queries):
    from core.services.meeting_corrections import correction_baseline, confirm_correction, effective_progress_updates
    from core.services.manual_meetings import ManualMeetingError
    task = Task.objects.create(project=Project.objects.create(name="P"), title="T", progress=20)
    note = MeetingNote.objects.create(title="M", meeting_date=timezone.localdate(), raw_text="unchanged")
    update = ProgressUpdate.objects.create(task=task, meeting_note=note, new_progress=20, new_status="in_progress", occurred_on=note.meeting_date)
    baseline = correction_baseline(update)
    arguments = dict(reason="录错", proposed={"new_progress":40}, baseline=baseline, token="one", apply_current=True)
    first = confirm_correction(update.pk, **arguments)
    assert confirm_correction(update.pk, **arguments).pk == first.pk
    task.refresh_from_db(); assert task.progress == 40
    with pytest.raises(ManualMeetingError, match="已有更正"):
        confirm_correction(update.pk, **{**arguments,"token":"stale"})
    update.refresh_from_db()
    fresh = correction_baseline(update)
    ProgressUpdate.objects.create(task=task, new_progress=80, new_status="in_progress")
    with pytest.raises(ManualMeetingError, match="后续进展"):
        confirm_correction(update.pk, **{**arguments,"baseline":fresh,"token":"later"})
    second = confirm_correction(update.pk, **{**arguments,"baseline":fresh,"token":"history", "apply_current":False, "proposed":{"new_progress":50}})
    task.refresh_from_db(); assert task.progress == 40
    assert second.pk != first.pk
    with django_assert_num_queries(2):
        rows = effective_progress_updates(ProgressUpdate.objects.order_by("pk"))
    assert rows[0].new_progress == 50 and rows[1].new_progress == 80
    assert rows[0].applied_correction.pk == second.pk
    update.refresh_from_db(); assert update.new_progress == 20

@pytest.mark.django_db
def test_risk_conflict_and_missing_close_reason():
    from core.models import Risk
    from core.services.risk_followups import risk_baseline, confirm_followup
    from core.services.manual_meetings import ManualMeetingError
    risk = Risk.objects.create(project=Project.objects.create(name="P"), content="risk")
    note = MeetingNote.objects.create(title="M", meeting_date=timezone.localdate(), raw_text="")
    args = dict(response="答复",next_step="",reason="",status="closed",owner_id=None,due_date="",baseline=risk_baseline(risk),token="c")
    with pytest.raises(ManualMeetingError, match="理由"):
        confirm_followup(risk.pk,note,**args)
    risk.content="其他页修改"; risk.save()
    with pytest.raises(ManualMeetingError, match="其他页面"):
        confirm_followup(risk.pk,note,**{**args,"reason":"完成"})
    assert risk.followups.count() == 0

@pytest.mark.django_db
def test_http_correction_requires_preview_and_preserves_other_entry(admin_client):
    from django.urls import reverse
    from core.services.meeting_corrections import correction_baseline
    task = Task.objects.create(project=Project.objects.create(name="P"),title="T",progress=70)
    note = MeetingNote.objects.create(title="AI",meeting_date=timezone.localdate()-timedelta(days=1),raw_text="原纪要不变")
    update = ProgressUpdate.objects.create(task=task,meeting_note=note,new_progress=20,new_status="in_progress")
    other = ProgressUpdate.objects.create(task=task,meeting_note=note,new_progress=25,new_status="in_progress")
    url = reverse("progress_correction",args=[update.pk])
    assert admin_client.get(url).status_code == 200
    preview = admin_client.post(url,dict(reason="更正说明",new_status="in_progress",new_progress=30,completed_work="真实完成",next_step="继续",baseline=correction_baseline(update),token="http"))
    assert preview.status_code == 200 and "拟更正值" in preview.content.decode()
    assert not update.corrections.exists()
    confirmation = preview.context["confirmation"]
    response = admin_client.post(url,{"confirmation":confirmation})
    assert response.status_code == 302
    assert admin_client.post(url,{"confirmation":confirmation}).status_code == 302
    assert update.corrections.count() == 1 and not other.corrections.exists()
    assert admin_client.get(reverse("meeting_history",args=[note.pk])).status_code == 200
    note.refresh_from_db(); assert note.raw_text == "原纪要不变"

@pytest.mark.django_db
def test_http_risk_followup_selection_stash_and_conflict(admin_client):
    from django.urls import reverse
    from core.models import Risk
    from core.services.manual_meetings import create_session
    from core.services.risk_followups import risk_baseline
    project = Project.objects.create(name="P")
    risk = Risk.objects.create(project=project,content="事项")
    session = create_session({"title":"会","meeting_date":timezone.localdate().isoformat(),"project_ids":[project.pk],"items":[]})
    url = reverse("session_followups",args=[session.pk])
    assert admin_client.get(url+f"?risk={risk.pk}").status_code == 200
    data = dict(risk=risk.pk,response="答复",next_step="实施",status="tracking",owner="",due_date="",reason="",baseline=risk_baseline(risk),token="webdraft",version=0)
    assert admin_client.post(url,data).status_code == 302
    risk.refresh_from_db(); assert risk.status == "open" and risk.followups.count() == 0
    assert admin_client.post(url,{**data,"response":"陈旧页面覆盖"}).status_code == 409

@pytest.mark.django_db(transaction=True)
def test_concurrent_corrections_cannot_both_apply():
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from django.db import connections, OperationalError
    from core.services.meeting_corrections import correction_baseline, confirm_correction
    from core.services.manual_meetings import ManualMeetingError
    task = Task.objects.create(project=Project.objects.create(name="P"),title="T",progress=20)
    note = MeetingNote.objects.create(title="M",meeting_date=timezone.localdate(),raw_text="original")
    update = ProgressUpdate.objects.create(task=task,meeting_note=note,new_progress=20,new_status="in_progress",occurred_on=note.meeting_date)
    baseline = correction_baseline(update)
    barrier = Barrier(2)
    def confirm(index):
        try:
            barrier.wait(timeout=5)
            return confirm_correction(update.pk,reason="核对",proposed={"new_progress":30+index},baseline=baseline,token=f"concurrent-{index}",apply_current=True).pk
        except (ManualMeetingError, OperationalError):
            return None
        finally:
            connections.close_all()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(confirm,[0,1]))
    assert len([result for result in results if result]) == 1
    assert update.corrections.count() == 1
    task.refresh_from_db(); assert task.progress in {30,31}

@pytest.mark.django_db
def test_current_correction_detects_task_edit_and_reason_required():
    from core.services.meeting_corrections import correction_baseline, confirm_correction
    from core.services.manual_meetings import ManualMeetingError
    task = Task.objects.create(project=Project.objects.create(name="P"),title="T",progress=20)
    note = MeetingNote.objects.create(title="M",meeting_date=timezone.localdate(),raw_text="original")
    update = ProgressUpdate.objects.create(task=task,meeting_note=note,new_progress=20,new_status="in_progress",occurred_on=note.meeting_date)
    args = dict(reason="",proposed={"new_progress":40},baseline=correction_baseline(update),token="invalid",apply_current=True)
    with pytest.raises(ManualMeetingError, match="原因"):
        confirm_correction(update.pk,**args)
    task.title="改名"; task.save()
    with pytest.raises(ManualMeetingError, match="修改"):
        confirm_correction(update.pk,**{**args,"reason":"修正"})
    assert update.corrections.count() == 0

@pytest.mark.django_db
def test_replaying_meeting_confirmation_does_not_duplicate_followup():
    from core.models import Risk
    from core.services.risk_followups import risk_baseline
    from core.services.manual_meetings import create_session, confirm_session
    risk = Risk.objects.create(project=Project.objects.create(name="P"),content="风险")
    session=create_session({"title":"M","meeting_date":timezone.localdate().isoformat(),"items":[],"risk_followups":[
        dict(risk_id=risk.pk,response="无变化，继续等",next_step="追问",reason="",status="open",owner_id=None,due_date="",baseline=risk_baseline(risk),token="no-change")]})
    confirm_session(session.pk,0)
    confirm_session(session.pk,0)
    assert risk.followups.count() == 1

@pytest.mark.django_db
def test_same_key_rejects_changed_correction_and_followup_payloads():
    from core.models import Risk
    from core.services.meeting_corrections import correction_baseline, confirm_correction
    from core.services.risk_followups import risk_baseline, confirm_followup
    from core.services.manual_meetings import ManualMeetingError
    project = Project.objects.create(name="P")
    task = Task.objects.create(project=project,title="T",progress=20)
    note = MeetingNote.objects.create(title="M",meeting_date=timezone.localdate(),raw_text="")
    update=ProgressUpdate.objects.create(task=task,meeting_note=note,new_progress=20,new_status="in_progress")
    args=dict(reason=" 原因 ",proposed={"new_status":"done","new_progress":50,"completed_work":" 完成 "},baseline=correction_baseline(update),token="same-c",apply_current=False)
    result=confirm_correction(update.pk,**args)
    assert confirm_correction(update.pk,**{**args,"reason":"原因","proposed":{"completed_work":"完成","new_progress":100,"new_status":"done"},"baseline":"retry-does-not-recheck-stale-baseline"}).pk==result.pk
    for change in ({"reason":"其他原因"},{"proposed":{"new_progress":60}},{"apply_current":True}):
        with pytest.raises(ManualMeetingError, match="内容"):
            confirm_correction(update.pk,**{**args,**change})
    risk=Risk.objects.create(project=project,content="R")
    follow=dict(response=" 答复 ",next_step=" 下一步 ",reason="",status="tracking",owner_id=None,due_date="",baseline=risk_baseline(risk),token="same-r")
    recorded=confirm_followup(risk.pk,note,**follow)
    assert confirm_followup(risk.pk,note,**{**follow,"response":"答复","baseline":"stale-retry"}).pk==recorded.pk
    for change in ({"response":"新答复"},{"next_step":"变化"},{"status":"open"},{"due_date":"2026-10-01"}):
        with pytest.raises(ManualMeetingError,match="内容"):
            confirm_followup(risk.pk,note,**{**follow,**change})

@pytest.mark.django_db
def test_followup_ajax_ack_and_preview_owner_date_changes(admin_client):
    from django.urls import reverse
    from core.models import Risk, Person
    from core.services.risk_followups import risk_baseline
    from core.services.manual_meetings import create_session, preview_session
    project=Project.objects.create(name="P")
    owner=Person.objects.create(name="原负责人")
    risk=Risk.objects.create(project=project,content="R",owner=owner,due_date=timezone.localdate())
    session=create_session({"title":"M","meeting_date":timezone.localdate().isoformat(),"items":[]})
    data=dict(risk=risk.pk,response="回答",next_step="继续",status="tracking",owner="",due_date="",reason="",baseline=risk_baseline(risk),token="ajax",version=0)
    url=reverse("session_followups",args=[session.pk])
    response=admin_client.post(url,data,HTTP_ACCEPT="application/json")
    assert response.status_code==200 and response.json()["version"]==1
    retry=admin_client.post(url,data,HTTP_ACCEPT="application/json")
    assert retry.status_code==200 and retry.json()["version"]==1
    conflict=admin_client.post(url,{**data,"response":"其他答复"},HTTP_ACCEPT="application/json")
    assert conflict.status_code==409 and not conflict.json()["ok"]
    invalid=admin_client.post(url,{**data,"response":""},HTTP_ACCEPT="application/json")
    assert invalid.status_code==400 and not invalid.json()["ok"]
    session.refresh_from_db()
    minutes=preview_session(session)["minutes"]
    assert "负责人：原负责人 → 未指定" in minutes
    assert f"目标日期：{timezone.localdate().isoformat()} → 未设置" in minutes

@pytest.mark.django_db
def test_correction_pages_show_chinese_status_labels(admin_client):
    from django.urls import reverse
    from core.services.meeting_corrections import correction_baseline
    task=Task.objects.create(project=Project.objects.create(name="P"),title="T",status="in_progress")
    note=MeetingNote.objects.create(title="M",meeting_date=timezone.localdate(),raw_text="")
    update=ProgressUpdate.objects.create(task=task,meeting_note=note,new_progress=20,new_status="in_progress")
    url=reverse("progress_correction",args=[update.pk])
    page=admin_client.get(url).content.decode()
    assert "原进展：进行中" in page and "报告有效值：进行中" in page
    preview=admin_client.post(url,dict(reason="修正",new_status="done",new_progress=100,completed_work="完成",next_step="",baseline=correction_baseline(update),token="labels"))
    assert "拟更正状态：已完成" in preview.content.decode()
    admin_client.post(url,{"confirmation":preview.context["confirmation"]})
    history=admin_client.get(reverse("meeting_history",args=[note.pk])).content.decode()
    assert "原值：进行中" in history and "更正值：已完成" in history
