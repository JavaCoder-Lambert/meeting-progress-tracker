from datetime import date

import pytest
from pydantic import ValidationError

from core.services.llm_schema import ParsedTask, normalize_date


def test_yearless_date_uses_meeting_year():
    assert normalize_date("9月16日", date(2026, 8, 20)) == date(2026, 9, 16)


def test_compact_date_uses_meeting_year():
    assert normalize_date("0828", date(2026, 8, 20)) == date(2026, 8, 28)


def test_invalid_progress_is_rejected():
    with pytest.raises(ValidationError):
        ParsedTask(title="联调", progress=120)
