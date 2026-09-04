import re
from datetime import date
from html.parser import HTMLParser
from pathlib import Path

import pytest

from core.models import ImportDraft, MeetingNote


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class _FormLabelParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.control_ids = set()
        self.label_targets = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag in {"input", "select", "textarea"} and attributes.get("id"):
            self.control_ids.add(attributes["id"])
        if tag == "label":
            self.label_targets.append(attributes.get("for"))


def _relative_luminance(color):
    channels = [int(color[index:index + 2], 16) / 255 for index in (0, 2, 4)]
    linear = [channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4 for channel in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast_ratio(first, second):
    lighter, darker = sorted((_relative_luminance(first), _relative_luminance(second)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def _css_color(css, variable):
    match = re.search(rf"{re.escape(variable)}\s*:\s*#([0-9a-fA-F]{{6}})", css)
    assert match, f"Missing CSS color variable {variable}"
    return match.group(1)


@pytest.mark.django_db
def test_draft_review_labels_reference_their_controls(admin_client):
    note = MeetingNote.objects.create(title="周会", meeting_date=date(2026, 9, 4), raw_text="记录")
    draft = ImportDraft.objects.create(meeting_note=note, payload={
        "summary": "摘要",
        "uncertainties": [],
        "tasks": [{
            "title": "联调", "project_name": "", "assignee_name": "", "description": "接口说明",
            "planned_start_date": None, "due_date": None, "acceptance_date": None,
            "status": "in_progress", "priority": "normal", "progress": 20,
            "current_note": "处理中", "completed_work": "接口已通", "next_step": "联测",
        }],
        "risks": [{
            "project_name": "", "task_title": "联调", "risk_type": "risk", "content": "排期风险",
            "owner_name": "", "due_date": None, "status": "open",
        }],
        "milestones": [{
            "project_name": "", "name": "上线", "description": "", "target_date": None,
            "status": "not_started",
        }],
    })

    response = admin_client.get(f"/drafts/{draft.id}/")
    parser = _FormLabelParser()
    parser.feed(response.content.decode())

    assert None not in parser.label_targets
    assert set(parser.label_targets) <= parser.control_ids


def test_parse_failure_script_updates_badge_and_shows_server_message():
    script = (PROJECT_ROOT / "static/js/app.js").read_text()
    failure_handler = script.split("} catch (error) {", 1)[1].split("} finally", 1)[0]

    assert '[data-note-status]' in script
    assert "status-failed" in failure_handler
    assert "progressDetail.textContent = error.message" in failure_handler


def test_task_update_script_requires_an_existing_task():
    script = (PROJECT_ROOT / "static/js/app.js").read_text()

    assert "existingSelect.required = updating" in script


def test_mobile_styles_keep_logout_available():
    css = (PROJECT_ROOT / "static/css/app.css").read_text()

    assert not re.search(r"\.logout-form\s*\{[^}]*display\s*:\s*none", css, re.DOTALL)


def test_muted_text_colors_meet_wcag_aa_contrast():
    css = (PROJECT_ROOT / "static/css/app.css").read_text()
    muted = _css_color(css, "--muted")

    assert _contrast_ratio(muted, _css_color(css, "--paper")) >= 4.5
    assert _contrast_ratio(muted, _css_color(css, "--white")) >= 4.5
