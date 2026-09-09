from datetime import date, timedelta
from io import StringIO
import os
import signal

import pytest
from django.test import Client, override_settings
from django.db import connection
from django.core.management import call_command, CommandError
from django.utils import timezone

from core.models import ImportDraft, MeetingNote, ParseJob
from core.services.llm_client import LLMParseError
from core.services.parse_jobs import claim_next_job, enqueue_parse, finish_job, recover_expired_jobs, run_next_job


PAYLOAD = {"summary": "接口完成", "tasks": [], "risks": [], "milestones": [], "uncertainties": []}


@pytest.fixture
def note(db):
    return MeetingNote.objects.create(title="周会", meeting_date=date(2026, 9, 5), raw_text="接口完成")


@pytest.mark.django_db
@override_settings(LLM_API_KEY="fake", LLM_MODEL="fake")
def test_enqueue_is_durable_and_duplicates_share_one_job_without_calling_model(note, monkeypatch):
    def unexpected(*args):
        raise AssertionError("提交不可调用模型")

    monkeypatch.setattr("core.services.parse_jobs.generate_meeting_payload", unexpected)
    first = enqueue_parse(note)
    second = enqueue_parse(note)

    assert first.pk == second.pk
    assert ParseJob.objects.count() == 1
    assert first.status == ParseJob.Status.QUEUED
    assert ImportDraft.objects.count() == 0
    note.refresh_from_db()
    assert note.parse_status == MeetingNote.ParseStatus.PARSING


@pytest.mark.django_db(transaction=True)
@override_settings(LLM_API_KEY="fake", LLM_MODEL="fake")
def test_worker_network_wait_is_outside_transaction_and_publishes_draft_atomically(monkeypatch):
    meeting = MeetingNote.objects.create(title="周会", meeting_date=date(2026, 9, 5), raw_text="原文")
    job = enqueue_parse(meeting)

    def model(note):
        assert connection.in_atomic_block is False
        assert note.pk == meeting.pk
        return "response", PAYLOAD

    monkeypatch.setattr("core.services.parse_jobs.generate_meeting_payload", model)
    assert run_next_job() is True
    assert run_next_job() is False
    job.refresh_from_db(); meeting.refresh_from_db()
    assert job.status == ParseJob.Status.SUCCESS
    assert job.draft.payload == PAYLOAD
    assert meeting.parse_status == MeetingNote.ParseStatus.SUCCESS
    assert meeting.raw_llm_response == "response"
    assert job.attempts == 1


@pytest.mark.django_db
@override_settings(LLM_API_KEY="fake", LLM_MODEL="fake")
def test_expired_attempt_requires_explicit_retry_and_late_result_cannot_overwrite(note):
    enqueue_parse(note)
    old = claim_next_job()
    assert claim_next_job() is None
    ParseJob.objects.filter(pk=old.pk).update(lease_expires_at=timezone.now() - timedelta(seconds=1))
    assert recover_expired_jobs() == 1
    assert claim_next_job() is None
    note.refresh_from_db()
    assert note.parse_status == MeetingNote.ParseStatus.FAILED
    enqueue_parse(note)
    current = claim_next_job()
    assert current.lease_token != old.lease_token
    assert finish_job(old, content="旧结果", payload=PAYLOAD) is False
    assert finish_job(current, content="新结果", payload=PAYLOAD) is True
    note.refresh_from_db()
    assert note.raw_llm_response == "新结果"
    assert ImportDraft.objects.count() == 1


@pytest.mark.django_db
@override_settings(LLM_API_KEY="fake", LLM_MODEL="fake")
@pytest.mark.parametrize("exception, expected", [
    (LLMParseError("大模型响应超时"), "大模型响应超时"),
    (RuntimeError("unexpected key or response: sensitive"), "解析暂时未能完成"),
])
def test_worker_failure_is_persistent_sanitized_and_retryable(note, monkeypatch, exception, expected):
    job = enqueue_parse(note)

    def fail(_note):
        raise exception

    monkeypatch.setattr("core.services.parse_jobs.generate_meeting_payload", fail)
    run_next_job()
    note.refresh_from_db(); job.refresh_from_db()
    assert job.status == ParseJob.Status.FAILED
    assert expected in note.parse_error
    assert "sensitive" not in job.error
    assert ImportDraft.objects.count() == 0
    assert enqueue_parse(note).status == ParseJob.Status.QUEUED


@pytest.mark.django_db
@override_settings(LLM_API_KEY="fake", LLM_MODEL="fake")
def test_failed_draft_write_rolls_back_note_and_job_success(note, monkeypatch):
    enqueue_parse(note)
    job = claim_next_job()

    def fail(**kwargs):
        raise RuntimeError("storage unavailable")

    monkeypatch.setattr(ImportDraft.objects, "create", fail)
    with pytest.raises(RuntimeError):
        finish_job(job, content="new response", payload=PAYLOAD)
    note.refresh_from_db(); job.refresh_from_db()
    assert note.parse_status == MeetingNote.ParseStatus.PARSING
    assert note.raw_llm_response == ""
    assert job.status == ParseJob.Status.RUNNING


@pytest.mark.django_db
def test_imported_history_blocks_enqueue_even_if_note_state_is_stale(note):
    note.parse_status = MeetingNote.ParseStatus.SUCCESS
    note.save()
    ImportDraft.objects.create(meeting_note=note, payload=PAYLOAD, confirmed_at=timezone.now())
    with pytest.raises(LLMParseError, match="已入库"):
        enqueue_parse(note)
    assert ParseJob.objects.count() == 0


@pytest.mark.django_db
@override_settings(LLM_API_KEY="fake", LLM_MODEL="fake")
def test_status_and_reload_resume_existing_job_then_link_to_worker_result(admin_client, note, monkeypatch):
    first = admin_client.post(f"/meetings/{note.pk}/parse/", HTTP_X_REQUESTED_WITH="XMLHttpRequest")
    second = admin_client.post(f"/meetings/{note.pk}/parse/", HTTP_X_REQUESTED_WITH="XMLHttpRequest")
    assert first.status_code == second.status_code == 202
    assert ParseJob.objects.count() == 1
    detail = admin_client.get(f"/meetings/{note.pk}/")
    assert detail.context["parse_active"] is True
    assert 'data-parse-state="queued"' in detail.content.decode()
    status = admin_client.get(first.json()["status_url"]).json()
    assert status["state"] == "queued"
    assert not {"raw_text", "raw_llm_response", "payload", "key"}.intersection(status)
    monkeypatch.setattr("core.services.parse_jobs.generate_meeting_payload", lambda _: ("response", PAYLOAD))
    run_next_job()
    finished = admin_client.get(first.json()["status_url"]).json()
    assert finished["state"] == "success"
    assert finished["redirect_url"] == f"/drafts/{ImportDraft.objects.get().pk}/"


@pytest.mark.django_db
def test_status_requires_login_and_is_read_only_method(client, admin_client, note):
    assert Client().get(f"/meetings/{note.pk}/parse-status/").status_code == 302
    assert admin_client.post(f"/meetings/{note.pk}/parse-status/").status_code == 405


@pytest.mark.django_db
def test_meeting_inbox_pages_in_sql_without_fetching_raw_content_or_draft_payload(admin_client, django_assert_num_queries):
    notes = MeetingNote.objects.bulk_create([
        MeetingNote(title=f"会议 {i}", meeting_date=date(2026, 9, 5), raw_text="very large secret body")
        for i in range(45)
    ])
    ImportDraft.objects.bulk_create([ImportDraft(meeting_note=n, payload={"summary": "large payload"}) for n in notes])
    # Session + user + COUNT + project picker + one annotated, paginated list query.
    with django_assert_num_queries(5) as captured:
        first = admin_client.get("/meetings/")
    assert len(first.context["meeting_list"]) == 20
    assert first.context["page_obj"].paginator.count == 45
    page_query = captured.captured_queries[-1]["sql"]
    assert "LIMIT 20" in page_query
    assert '"raw_text"' not in page_query
    assert '"raw_llm_response"' not in page_query
    assert '"payload"' not in page_query
    last = admin_client.get("/meetings/?page=3")
    assert len(last.context["meeting_list"]) == 5
    assert not set(first.context["meeting_list"]).intersection(last.context["meeting_list"])


@pytest.mark.django_db(transaction=True)
def test_worker_command_exposes_heartbeat_before_work_and_check_rejects_stale_file(tmp_path, monkeypatch):
    from core.management.commands import run_parse_worker

    heartbeat = tmp_path / "heartbeat"
    monkeypatch.setattr(run_parse_worker, "HEARTBEAT_PATH", heartbeat)
    with pytest.raises(CommandError, match="尚未启动"):
        call_command("run_parse_worker", check=True, stdout=StringIO())

    def work():
        assert heartbeat.exists()
        call_command("run_parse_worker", check=True, stdout=StringIO())
        return False

    monkeypatch.setattr(run_parse_worker, "run_next_job", work)
    call_command("run_parse_worker", once=True, stdout=StringIO())
    os.utime(heartbeat, (0, 0))
    with pytest.raises(CommandError, match="已过期"):
        call_command("run_parse_worker", check=True, stdout=StringIO())


@pytest.mark.django_db(transaction=True)
@override_settings(LLM_API_KEY="fake", LLM_MODEL="fake")
def test_worker_sigterm_finishes_current_job_without_claiming_the_next(tmp_path, monkeypatch):
    from core.management.commands import run_parse_worker

    monkeypatch.setattr(run_parse_worker, "HEARTBEAT_PATH", tmp_path / "heartbeat")
    first = MeetingNote.objects.create(title="当前会议", meeting_date=date(2026, 9, 5), raw_text="原文")
    second = MeetingNote.objects.create(title="下个会议", meeting_date=date(2026, 9, 5), raw_text="原文")
    first_job, second_job = enqueue_parse(first), enqueue_parse(second)
    previous = signal.getsignal(signal.SIGTERM)

    def finish_after_shutdown_request(note):
        assert note.pk == first.pk
        # Exercise the installed handler without sending a signal to pytest.
        signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
        return "finished in grace period", PAYLOAD

    monkeypatch.setattr("core.services.parse_jobs.generate_meeting_payload", finish_after_shutdown_request)
    call_command("run_parse_worker", stdout=StringIO())
    first_job.refresh_from_db(); second_job.refresh_from_db()
    assert first_job.status == ParseJob.Status.SUCCESS
    assert second_job.status == ParseJob.Status.QUEUED
    assert second_job.attempts == 0
    assert signal.getsignal(signal.SIGTERM) == previous
