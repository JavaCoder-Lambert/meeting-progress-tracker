import asyncio
from datetime import date
import io
import json
import logging
import logging.config

import pytest

from core.models import MeetingNote, ParseJob, Project, Task
from core.services import llm_client, parse_jobs
from tracker import settings as tracker_settings


pytestmark = pytest.mark.django_db


class RecordCollector(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


def test_context_omits_tasks_belonging_to_archived_projects():
    active = Project.objects.create(name="当前项目")
    archived = Project.objects.create(name="归档项目", status=Project.Status.ARCHIVED)
    Task.objects.create(project=active, title="当前任务")
    Task.objects.create(project=archived, title="归档任务")

    context = llm_client._context()

    assert [item["title"] for item in context["open_tasks"]] == ["当前任务"]


def test_generate_logs_safe_stage_timings_and_counts_without_sensitive_content(monkeypatch):
    note = MeetingNote.objects.create(title="周会", meeting_date=date(2026, 9, 9), raw_text="机密会议原文")
    monkeypatch.setattr(llm_client.settings, "LLM_API_KEY", "secret-api-key")
    monkeypatch.setattr(llm_client.settings, "LLM_MODEL", "fake-model")
    responses = iter(["not json secret-response", '{"summary":"完成","tasks":[],"risks":[],"milestones":[],"uncertainties":[]}'])

    async def fake_request(client, body):
        return next(responses)

    monkeypatch.setattr(llm_client, "_request_content", fake_request)
    collector = RecordCollector()
    llm_client.logger.addHandler(collector)
    try:
        _, payload = llm_client.generate_meeting_payload(note)
    finally:
        llm_client.logger.removeHandler(collector)

    stages = [record.parse_stage for record in collector.records if hasattr(record, "parse_stage")]
    assert stages == ["context", "first_call", "repair_call", "model_total"]
    assert payload["summary"] == "完成"
    assert collector.records[0].input_chars == len(note.raw_text)
    assert all(record.duration_ms >= 0 for record in collector.records if hasattr(record, "duration_ms"))
    diagnostic_text = " ".join(record.getMessage() + repr(record.__dict__) for record in collector.records)
    for secret in (note.raw_text, "secret-api-key", "secret-response"):
        assert secret not in diagnostic_text


def test_worker_logs_persistence_duration_and_result_counts(monkeypatch):
    note = MeetingNote.objects.create(title="周会", meeting_date=date(2026, 9, 9), raw_text="不要记录我")
    ParseJob.objects.create(meeting_note=note)
    monkeypatch.setattr(parse_jobs.settings, "LLM_API_KEY", "fake-key")
    monkeypatch.setattr(parse_jobs.settings, "LLM_MODEL", "fake-model")
    payload = {"summary": "摘要", "tasks": [{"title": "一"}], "risks": [], "milestones": [], "uncertainties": []}
    monkeypatch.setattr(parse_jobs, "generate_meeting_payload", lambda unused: ("private response", payload))
    collector = RecordCollector()
    parse_jobs.logger.addHandler(collector)
    try:
        assert parse_jobs.run_next_job() is True
    finally:
        parse_jobs.logger.removeHandler(collector)

    records = [record for record in collector.records if hasattr(record, "parse_stage")]
    assert [record.parse_stage for record in records] == ["persistence", "total"]
    record = records[0]
    assert record.duration_ms >= 0
    assert record.task_count == 1
    assert "private response" not in record.getMessage() + repr(record.__dict__)
    assert records[1].duration_ms >= record.duration_ms


def test_default_django_logging_emits_whitelisted_json_without_forcing_caplog_level():
    logging.config.dictConfig(tracker_settings.LOGGING)
    logger = logging.getLogger("core.services.llm_client")
    configured = logger.handlers[0]
    stream = io.StringIO()
    configured.setStream(stream)
    root_stream = io.StringIO()
    root_handler = logging.StreamHandler(root_stream)
    logging.getLogger().addHandler(root_handler)
    try:
        logger.info("sensitive message must be discarded", extra={
            "parse_stage": "context", "meeting_note_id": 7, "duration_ms": 1.25,
            "task_count": 3, "input_chars": 19,
            "raw_text": "private meeting", "api_key": "private key",
        })
        logger.warning("ordinary warning without diagnostic metadata")
    finally:
        logging.getLogger().removeHandler(root_handler)

    emitted = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert emitted == [
        {"event": "llm_parse_stage", "stage": "context", "meeting_note_id": 7,
         "duration_ms": 1.25, "task_count": 3, "input_chars": 19},
        {"event": "llm_parse_stage", "stage": "unknown"},
    ]
    assert root_stream.getvalue() == ""
