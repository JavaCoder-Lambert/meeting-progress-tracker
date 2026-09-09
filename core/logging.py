"""Safe structured logging for LLM parsing diagnostics."""
import json
import logging


class LLMParseFormatter(logging.Formatter):
    """Serialize only an explicit metadata allowlist, never the log message."""

    FIELDS = (
        "meeting_note_id", "duration_ms", "project_count", "people_count",
        "task_count", "risk_count", "milestone_count", "uncertainty_count", "input_chars",
    )

    def format(self, record):
        output = {"event": "llm_parse_stage", "stage": getattr(record, "parse_stage", "unknown")}
        output.update({field: getattr(record, field) for field in self.FIELDS if hasattr(record, field)})
        return json.dumps(output, ensure_ascii=False, separators=(",", ":"))
