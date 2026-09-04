import json

import httpx
from django.conf import settings

from core.models import ImportDraft, MeetingNote, Person, Project, Task
from .llm_schema import ParsedMeeting


class LLMParseError(Exception):
    pass


SYSTEM_PROMPT = """你是项目会议记录整理助手。只返回 JSON，不要 Markdown。根据 schema 提取任务、风险和里程碑。没有明确的信息使用空字符串或 null，不要猜测。状态使用英文枚举。相同事项若看起来是现有任务，也仍放入 tasks，由用户确认更新。"""


def _context():
    return {
        "projects": list(Project.objects.exclude(status=Project.Status.ARCHIVED).values_list("name", flat=True)),
        "people": list(Person.objects.filter(is_active=True).values_list("name", flat=True)),
        "open_tasks": list(Task.objects.exclude(status=Task.Status.DONE).values("id", "title", "project__name", "assignee__name", "status", "progress")[:300]),
    }


def parse_meeting_note(note: MeetingNote) -> ImportDraft:
    if not settings.LLM_API_KEY or not settings.LLM_MODEL:
        raise LLMParseError("大模型配置不完整，请设置 LLM_API_KEY 和 LLM_MODEL。")
    note.parse_status = MeetingNote.ParseStatus.PARSING
    note.parse_error = ""
    note.save(update_fields=["parse_status", "parse_error", "updated_at"])
    body = {
        "model": settings.LLM_MODEL,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps({"meeting_date": note.meeting_date.isoformat(), "context": _context(), "schema": ParsedMeeting.model_json_schema(), "meeting_text": note.raw_text}, ensure_ascii=False)},
        ],
    }
    try:
        response = httpx.post(
            f"{settings.LLM_BASE_URL.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {settings.LLM_API_KEY}"},
            json=body,
            timeout=settings.LLM_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        parsed = ParsedMeeting.model_validate_json(content)
    except httpx.TimeoutException as exc:
        message = "大模型请求超时，请稍后重试。"
        note.parse_status, note.parse_error = MeetingNote.ParseStatus.FAILED, message
        note.save(update_fields=["parse_status", "parse_error", "updated_at"])
        raise LLMParseError(message) from exc
    except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
        message = "大模型返回无法解析，请检查接口配置后重试。"
        note.parse_status, note.parse_error = MeetingNote.ParseStatus.FAILED, message
        note.save(update_fields=["parse_status", "parse_error", "updated_at"])
        raise LLMParseError(message) from exc
    note.raw_llm_response = content
    note.parse_status = MeetingNote.ParseStatus.SUCCESS
    note.save(update_fields=["raw_llm_response", "parse_status", "updated_at"])
    return ImportDraft.objects.create(meeting_note=note, payload=parsed.model_dump(mode="json"))
