import asyncio
import json
import logging
import re
import time

import httpx
from django.conf import settings
from django.db import transaction
from pydantic import ValidationError

from core.models import ImportDraft, MeetingNote, Person, Project, Task
from .llm_schema import ParsedMeeting


logger = logging.getLogger(__name__)


class LLMParseError(Exception):
    pass


SYSTEM_PROMPT = """你是项目会议记录整理助手。只输出一个不缩进的 JSON 对象，不要 Markdown。根对象使用 summary、tasks、risks、milestones、uncertainties，其中后四项均为数组。忠实提取任务、风险和里程碑，不要猜测。日期使用 YYYY-MM-DD。除必填字段外，省略原文未提供的字段、空值和默认值，让响应尽量精简。状态必须使用给定英文枚举。"""

OUTPUT_FORMAT = {
    "summary": "string",
    "tasks": {
        "required": ["title"],
        "optional": ["project_name", "assignee_name", "description", "planned_start_date", "due_date", "acceptance_date", "status", "priority", "progress", "current_note", "completed_work", "next_step"],
        "status": ["not_started", "in_progress", "acceptance", "done", "delayed", "blocked"],
        "priority": ["low", "normal", "high", "urgent"],
    },
    "risks": {
        "required": ["content"],
        "optional": ["project_name", "task_title", "risk_type", "owner_name", "due_date", "status"],
        "risk_type": ["risk", "blocker", "decision", "warning", "incident"],
        "status": ["open", "tracking", "resolved", "closed"],
    },
    "milestones": {
        "required": ["name"],
        "optional": ["project_name", "description", "target_date", "status"],
        "status": ["not_started", "in_progress", "done", "delayed"],
    },
    "uncertainties": ["string"],
}


def _context():
    return {
        "projects": list(Project.objects.exclude(status=Project.Status.ARCHIVED).values_list("name", flat=True)),
        "people": list(Person.objects.filter(is_active=True).values_list("name", flat=True)),
        "open_tasks": list(
            Task.objects.exclude(status=Task.Status.DONE).exclude(project__status=Project.Status.ARCHIVED)
            .values("id", "title", "project__name", "assignee__name", "status", "progress")[:100]
        ),
    }


def _request_body(note, context=None):
    prompt = {
        "meeting_date": note.meeting_date.isoformat(),
        "known_context": context if context is not None else _context(),
        "field_rules": OUTPUT_FORMAT,
        "meeting_text": note.raw_text,
    }
    return {
        "model": settings.LLM_MODEL,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(prompt, ensure_ascii=False, separators=(",", ":"))},
        ],
    }


async def _request_content(client, body):
    response = await client.post(
        f"{settings.LLM_BASE_URL.rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {settings.LLM_API_KEY}"},
        json=body,
    )
    response.raise_for_status()
    choice = response.json()["choices"][0]
    content = choice["message"]["content"]
    if not isinstance(content, str) or not content.strip():
        raise ValueError("empty model content")
    return content


def _diagnostic(stage, note_id, started, **counts):
    logger.info("LLM parsing stage completed", extra={
        "parse_stage": stage, "meeting_note_id": note_id,
        "duration_ms": round((time.monotonic() - started) * 1000, 3), **counts,
    })


async def _request_and_parse(body, note_id):
    async with asyncio.timeout(settings.LLM_TIMEOUT_SECONDS):
        async with httpx.AsyncClient(timeout=None) as client:
            started = time.monotonic()
            try:
                content = await _request_content(client, body)
            finally:
                _diagnostic("first_call", note_id, started)
            try:
                parsed = _parse_content(content)
            except (ValidationError, ValueError):
                started = time.monotonic()
                try:
                    content = await _request_content(client, _repair_body(body, content))
                finally:
                    _diagnostic("repair_call", note_id, started)
                parsed = _parse_content(content)
            return content, parsed


def _parse_content(content):
    text = content.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.IGNORECASE | re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    elif not text.startswith("{"):
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            text = text[start:end + 1]
    return ParsedMeeting.model_validate_json(text)


def _repair_body(body, invalid_content):
    repaired = {**body, "messages": list(body["messages"])}
    repaired["messages"].extend([
        {"role": "assistant", "content": invalid_content[:16000]},
        {
            "role": "user",
            "content": "上一个响应不是符合要求的 JSON。请修正字段、枚举和类型，省略空值和默认值，只返回精简的完整 JSON 对象。",
        },
    ])
    return repaired


def _fail(note, message, exc):
    note.parse_status = MeetingNote.ParseStatus.FAILED
    note.parse_error = message
    note.save(update_fields=["parse_status", "parse_error", "updated_at"])
    raise LLMParseError(message) from exc


def _http_error_message(exc):
    status = exc.response.status_code
    if status in {401, 403}:
        return "大模型鉴权失败，请检查 API Key 和模型权限。"
    if status == 429:
        return "大模型请求过于频繁，请稍后重试。"
    if status >= 500:
        return f"大模型服务暂时不可用（HTTP {status}），请稍后重试。"
    return f"大模型请求失败（HTTP {status}），请检查接口配置。"


def validate_llm_config():
    if not settings.LLM_API_KEY or not settings.LLM_MODEL:
        raise LLMParseError("大模型配置不完整，请设置 LLM_API_KEY 和 LLM_MODEL。")


def generate_meeting_payload(note: MeetingNote):
    """Wait for the provider without mutating persistent parsing state."""
    validate_llm_config()
    total_started = time.monotonic()
    context_started = time.monotonic()
    context = _context()
    _diagnostic("context", note.pk, context_started, project_count=len(context["projects"]),
                people_count=len(context["people"]), task_count=len(context["open_tasks"]),
                input_chars=len(note.raw_text))
    body = _request_body(note, context)
    result_counts = {"task_count": 0, "risk_count": 0, "milestone_count": 0, "uncertainty_count": 0}
    try:
        try:
            content, parsed = asyncio.run(_request_and_parse(body, note.pk))
        except (TimeoutError, httpx.TimeoutException) as exc:
            raise LLMParseError("大模型响应超时。复杂会议可能需要更久，请稍后重试或换用更快的模型。") from exc
        except httpx.HTTPStatusError as exc:
            raise LLMParseError(_http_error_message(exc)) from exc
        except httpx.RequestError as exc:
            raise LLMParseError("无法连接大模型服务，请检查网络或接口地址。") from exc
        except (ValidationError, KeyError, IndexError, TypeError, ValueError) as exc:
            raise LLMParseError("大模型返回内容格式不正确，已自动修复重试一次。请再次尝试。") from exc
        payload = parsed.model_dump(mode="json")
        for task, item in zip(parsed.tasks, payload["tasks"], strict=True):
            # Includes progress derived from an explicit done status by validation.
            item["_provided_fields"] = sorted(task.model_fields_set)
        result_counts = {"task_count": len(payload["tasks"]), "risk_count": len(payload["risks"]),
                         "milestone_count": len(payload["milestones"]),
                         "uncertainty_count": len(payload["uncertainties"])}
        return content, payload
    finally:
        _diagnostic("model_total", note.pk, total_started, **result_counts)


def parse_meeting_note(note: MeetingNote) -> ImportDraft:
    """Synchronous service retained for scripts; web requests enqueue a ParseJob."""
    validate_llm_config()
    note.parse_status = MeetingNote.ParseStatus.PARSING
    note.parse_error = ""
    note.save(update_fields=["parse_status", "parse_error", "updated_at"])
    try:
        content, payload = generate_meeting_payload(note)
    except LLMParseError as exc:
        _fail(note, str(exc), exc)
    with transaction.atomic():
        draft = ImportDraft.objects.create(meeting_note=note, payload=payload)
        note.raw_llm_response = content
        note.parse_status = MeetingNote.ParseStatus.SUCCESS
        note.parse_error = ""
        note.save(update_fields=["raw_llm_response", "parse_status", "parse_error", "updated_at"])
        return draft
