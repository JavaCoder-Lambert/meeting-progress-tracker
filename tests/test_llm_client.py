import asyncio
import json
from datetime import date

import httpx
import pytest
from django.test import override_settings

from core.models import ImportDraft, MeetingNote
from core.services.llm_client import LLMParseError, parse_meeting_note


VALID_PAYLOAD = {
    "summary": "已完成接口梳理",
    "tasks": [],
    "risks": [],
    "milestones": [],
    "uncertainties": [],
}


class FakeResponse:
    def __init__(self, content):
        self.content = content

    def raise_for_status(self):
        return None

    def json(self):
        return {"choices": [{"message": {"content": self.content}}]}


@pytest.fixture
def meeting_note(db):
    return MeetingNote.objects.create(
        title="项目周会",
        meeting_date=date(2026, 9, 4),
        raw_text="张川：本周完成接口梳理。",
    )


@pytest.mark.parametrize("wrapper", ["fence", "prose"])
@override_settings(LLM_API_KEY="test-key", LLM_MODEL="test-model")
def test_parse_accepts_json_with_common_model_wrappers(monkeypatch, meeting_note, wrapper):
    raw_json = json.dumps(VALID_PAYLOAD, ensure_ascii=False)
    content = f"```json\n{raw_json}\n```" if wrapper == "fence" else f"整理结果如下：\n{raw_json}\n请确认。"
    responses = iter([FakeResponse(content)])
    async def post(*args, **kwargs):
        return next(responses)

    monkeypatch.setattr("core.services.llm_client.httpx.AsyncClient.post", post)

    draft = parse_meeting_note(meeting_note)

    assert draft.payload["summary"] == "已完成接口梳理"
    meeting_note.refresh_from_db()
    assert meeting_note.parse_status == MeetingNote.ParseStatus.SUCCESS


@override_settings(LLM_API_KEY="test-key", LLM_MODEL="test-model")
def test_parse_retries_once_when_first_model_response_is_malformed(monkeypatch, meeting_note):
    responses = iter([FakeResponse("这不是 JSON"), FakeResponse(json.dumps(VALID_PAYLOAD, ensure_ascii=False))])
    async def post(*args, **kwargs):
        return next(responses)

    monkeypatch.setattr("core.services.llm_client.httpx.AsyncClient.post", post)

    draft = parse_meeting_note(meeting_note)

    assert draft.payload == VALID_PAYLOAD
    assert ImportDraft.objects.count() == 1


@override_settings(LLM_API_KEY="test-key", LLM_MODEL="test-model", LLM_TIMEOUT_SECONDS=1.1)
def test_parse_deadline_covers_initial_request_and_repair(monkeypatch, meeting_note):
    raw_json = json.dumps(VALID_PAYLOAD, ensure_ascii=False)
    async_call_count = 0

    async def slow_async_post(*args, **kwargs):
        nonlocal async_call_count
        async_call_count += 1
        await asyncio.sleep(0.02 if async_call_count == 1 else 1.2)
        return FakeResponse("这不是 JSON" if async_call_count == 1 else raw_json)

    monkeypatch.setattr("core.services.llm_client.httpx.AsyncClient.post", slow_async_post)

    with pytest.raises(LLMParseError, match="超时"):
        parse_meeting_note(meeting_note)

    meeting_note.refresh_from_db()
    assert meeting_note.parse_status == MeetingNote.ParseStatus.FAILED
    assert ImportDraft.objects.count() == 0


@override_settings(LLM_API_KEY="test-key", LLM_MODEL="test-model")
def test_parse_tolerates_null_optional_text_and_extra_model_metadata_without_retry(monkeypatch, meeting_note):
    payload = {
        **VALID_PAYLOAD,
        "tasks": [{"title": "接口梳理", "project_name": None, "confidence": 0.92}],
    }
    responses = iter([FakeResponse(json.dumps(payload, ensure_ascii=False))])
    async def post(*args, **kwargs):
        return next(responses)

    monkeypatch.setattr("core.services.llm_client.httpx.AsyncClient.post", post)

    draft = parse_meeting_note(meeting_note)

    assert draft.payload["tasks"][0]["project_name"] == ""
    assert "confidence" not in draft.payload["tasks"][0]


@override_settings(LLM_API_KEY="test-key", LLM_MODEL="test-model")
def test_compact_completed_task_gets_coherent_progress(monkeypatch, meeting_note):
    payload = {**VALID_PAYLOAD, "tasks": [{"title": "接口文档", "status": "done"}]}
    async def post(*args, **kwargs):
        return FakeResponse(json.dumps(payload, ensure_ascii=False))

    monkeypatch.setattr("core.services.llm_client.httpx.AsyncClient.post", post)

    draft = parse_meeting_note(meeting_note)

    assert draft.payload["tasks"][0]["progress"] == 100


@override_settings(LLM_API_KEY="test-key", LLM_MODEL="test-model")
def test_parse_reports_rate_limit_separately(monkeypatch, meeting_note):
    response = httpx.Response(
        429,
        request=httpx.Request("POST", "https://example.test/v1/chat/completions"),
    )
    async def post(*args, **kwargs):
        return response

    monkeypatch.setattr("core.services.llm_client.httpx.AsyncClient.post", post)

    with pytest.raises(LLMParseError, match="请求过于频繁"):
        parse_meeting_note(meeting_note)

    meeting_note.refresh_from_db()
    assert meeting_note.parse_status == MeetingNote.ParseStatus.FAILED
    assert "请求过于频繁" in meeting_note.parse_error


@override_settings(LLM_API_KEY="test-key", LLM_MODEL="test-model")
def test_parse_reports_timeout_without_creating_draft(monkeypatch, meeting_note):
    async def timeout(*args, **kwargs):
        request = httpx.Request("POST", "https://example.test/v1/chat/completions")
        raise httpx.ReadTimeout("timed out", request=request)

    monkeypatch.setattr("core.services.llm_client.httpx.AsyncClient.post", timeout)

    with pytest.raises(LLMParseError, match="超时") as caught:
        parse_meeting_note(meeting_note)

    meeting_note.refresh_from_db()
    assert meeting_note.parse_status == MeetingNote.ParseStatus.FAILED
    assert meeting_note.parse_error == str(caught.value)
    assert ImportDraft.objects.count() == 0


@override_settings(LLM_API_KEY="test-key", LLM_MODEL="test-model")
def test_parse_reports_provider_failure_without_creating_draft(monkeypatch, meeting_note):
    response = httpx.Response(
        503,
        request=httpx.Request("POST", "https://example.test/v1/chat/completions"),
    )
    async def post(*args, **kwargs):
        return response

    monkeypatch.setattr("core.services.llm_client.httpx.AsyncClient.post", post)

    with pytest.raises(LLMParseError, match="服务暂时不可用"):
        parse_meeting_note(meeting_note)

    meeting_note.refresh_from_db()
    assert meeting_note.parse_status == MeetingNote.ParseStatus.FAILED
    assert ImportDraft.objects.count() == 0


@override_settings(LLM_API_KEY="test-key", LLM_MODEL="test-model")
def test_parse_reports_format_failure_after_repair_attempt(monkeypatch, meeting_note):
    async def post(*args, **kwargs):
        return FakeResponse("不是 JSON")

    monkeypatch.setattr("core.services.llm_client.httpx.AsyncClient.post", post)

    with pytest.raises(LLMParseError, match="格式不正确"):
        parse_meeting_note(meeting_note)

    meeting_note.refresh_from_db()
    assert meeting_note.parse_status == MeetingNote.ParseStatus.FAILED
    assert ImportDraft.objects.count() == 0
