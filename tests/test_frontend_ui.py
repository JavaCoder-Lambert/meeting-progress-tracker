import re
import math
import subprocess
from datetime import date
from html.parser import HTMLParser
from pathlib import Path

import pytest
from django.contrib.staticfiles import finders

from core.models import ImportDraft, MeetingNote, Project, Task


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
    if match:
        return match.group(1)
    match = re.search(rf"{re.escape(variable)}\s*:\s*oklch\(([\d.]+)% ([\d.]+) ([\d.]+)\)", css)
    assert match, f"Missing CSS color variable {variable}"
    light, chroma, hue = map(float, match.groups())
    a, b = chroma * math.cos(math.radians(hue)), chroma * math.sin(math.radians(hue))
    l = (light / 100 + .3963377774 * a + .2158037573 * b) ** 3
    m = (light / 100 - .1055613458 * a - .0638541728 * b) ** 3
    s = (light / 100 - .0894841775 * a - 1.291485548 * b) ** 3
    channels = (4.0767416621*l - 3.3077115913*m + .2309699292*s,
                -1.2684380046*l + 2.6097574011*m - .3413193965*s,
                -.0041960863*l - .7034186147*m + 1.707614701*s)
    def channel(value):
        value = 12.92 * value if value <= .0031308 else 1.055 * value ** (1 / 2.4) - .055
        return f"{round(max(0, min(1, value)) * 255):02x}"
    return "".join(channel(value) for value in channels)


@pytest.mark.django_db
def test_draft_review_labels_reference_their_controls(admin_client):
    note = MeetingNote.objects.create(title="周会", meeting_date=date(2026, 9, 4), raw_text="记录")
    draft = ImportDraft.objects.create(meeting_note=note, payload={
        "summary": "摘要",
        "uncertainties": [],
        "tasks": [{
            "title": "联调", "project_name": "", "assignee_name": "", "description": "接口说明",
            "planned_start_date": None, "due_date": "日期待确认", "acceptance_date": None,
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
    html = response.content.decode()
    assert html.count("data-review-project") == 3
    assert html.count("data-review-date") == 5
    assert 'data-date-needs-correction="true"' in html


@pytest.mark.django_db
def test_failed_parse_has_accessible_server_message_and_retry_without_javascript(admin_client):
    note = MeetingNote.objects.create(title="失败会议", meeting_date=date(2026, 9, 5), raw_text="原文",
                                      parse_status="failed", parse_error="模型响应超时，请重试。")
    html = admin_client.get(f"/meetings/{note.pk}/").content.decode()
    assert 'role="alert"' in html
    assert "模型响应超时，请重试。" in html
    assert "重试解析" in html
    assert f'action="/meetings/{note.pk}/parse/"' in html


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


def test_action_summary_and_follow_up_text_have_narrow_screen_safe_styles():
    css = (PROJECT_ROOT / "static/css/app.css").read_text()
    dashboard = (PROJECT_ROOT / "templates/core/dashboard.html").read_text()

    assert re.search(r"\.action-counts\s*\{[^}]*grid-template-columns\s*:\s*repeat\(5,\s*1fr\)", css, re.DOTALL)
    assert re.search(r"\.action-counts\s+a\s*\{[^}]*display\s*:\s*block", css, re.DOTALL)
    assert re.search(r"\.follow-up-text\s*\{[^}]*white-space\s*:\s*pre-wrap", css, re.DOTALL)
    assert re.search(r"\.follow-up-text\s*\{[^}]*overflow-wrap\s*:\s*anywhere", css, re.DOTALL)
    assert 'class="follow-up-text"' in dashboard


@pytest.mark.django_db
def test_task_search_preserves_queue_and_exposes_keyboard_navigation(admin_client):
    project = Project.objects.create(name="项目")
    found = Task.objects.create(project=project, title="接口联调")
    Task.objects.create(project=project, title="其他任务")
    response = admin_client.get("/tasks/", {"q": "联调", "queue": "stale"})
    html = response.content.decode()
    assert 'href="#main-content"' in html
    assert 'id="main-content"' in html
    assert 'data-search-input' in html
    assert 'name="queue" value="stale"' in html
    assert 'data-shortcut-new-meeting' in html
    assert 'aria-keyshortcuts="n"' in html
    response = admin_client.get("/tasks/", {"q": "联调"})
    assert list(response.context["tasks"]) == [found]
    detail = admin_client.get(f"/tasks/{found.pk}/").content.decode()
    assert re.search(r'<a[^>]*aria-current="page"[^>]*href="/tasks/"', detail)


@pytest.mark.django_db
def test_task_surfaces_link_to_details_and_have_accessible_progress(admin_client):
    project = Project.objects.create(name="进度项目")
    task = Task.objects.create(project=project, title="可访问任务", progress=0)
    for url in ("/tasks/", "/tasks/board/", "/projects/", f"/tasks/{task.pk}/"):
        html = admin_client.get(url).content.decode()
        assert 'role="progressbar"' in html
        assert 'aria-valuemin="0"' in html
        assert 'aria-valuemax="100"' in html
        assert 'aria-valuenow="0"' in html
    for name in ("task_board", "project_detail", "person_detail"):
        template = (PROJECT_ROOT / f"templates/core/{name}.html").read_text()
        assert "'task_detail' task.pk" in template
        assert "'task_edit' task.pk" not in template
    html = admin_client.get("/tasks/").content.decode()
    assert '<caption' in html
    assert '<th scope="col">' in html
    assert 'tabindex="0"' in html


def test_copy_review_and_report_offer_accessible_progressive_interactions():
    base = (PROJECT_ROOT / "templates/base.html").read_text()
    review = (PROJECT_ROOT / "templates/core/draft_review.html").read_text()
    report = (PROJECT_ROOT / "templates/core/report.html").read_text()
    dashboard = (PROJECT_ROOT / "templates/core/dashboard.html").read_text()
    script = (PROJECT_ROOT / "static/js/app.js").read_text()
    assert 'data-copy-status' in base and 'aria-live="polite"' in base
    assert "favicon.svg" in base
    assert finders.find("favicon.svg")
    for template in (report, dashboard):
        assert 'data-copy-target=' in template
        assert 'onclick=' not in template
    for hook in ('data-review-filter', 'data-batch-action', 'data-review-summary',
                 'data-review-submit', 'data-recommended-action', 'data-action-label'):
        assert hook in review and hook in script
    assert 'diff.existing|default:"未填写"' not in review
    assert 'role="tablist"' in report and 'role="tabpanel"' in report
    assert 'data-report-tab' in report and 'data-report-tab' in script
    for behavior in ('ArrowRight', 'ArrowLeft', 'Home', 'End', 'isComposing',
                     'isContentEditable', 'ctrlKey', 'metaKey', 'altKey', 'shiftKey',
                     'clipboard.writeText'):
        assert behavior in script


def test_design_system_is_compact_accessible_and_reused():
    css = (PROJECT_ROOT / "static/css/app.css").read_text()
    templates = "\n".join(path.read_text() for path in (PROJECT_ROOT / "templates/core").glob("*.html"))
    for component in ('surface', 'data-list', 'data-row', 'summary-strip', 'filter-chip',
                      'empty-state', 'sticky-action-bar'):
        assert f'.{component}' in css and component in templates
    # Repeating lines convey week boundaries in the timeline, not decoration.
    assert 'gradient(' not in css.replace('repeating-linear-gradient(', 'week-grid(')
    assert 'backdrop-filter' not in css
    assert 'font-size: clamp' not in css
    assert 'min-height: 44px' in css
    assert ':focus-visible' in css
    assert 'prefers-reduced-motion' in css
    assert 'minmax(0, 1fr)' in css and 'overflow-x: auto' in css
    assert re.search(r'\.review-item-head\s*\{[^}]*margin(?:-bottom)?:\s*0', css)
    document = (PROJECT_ROOT / 'DESIGN.md').read_text()
    assert re.findall(r'^## (.+)$', document, re.MULTILINE) == [
        'Overview', 'Colors', 'Typography', 'Elevation', 'Components', "Do's and Don'ts",
    ]


@pytest.mark.django_db
def test_review_progress_difference_preserves_zero(admin_client):
    project = Project.objects.create(name="差异项目")
    task = Task.objects.create(project=project, title="接口联调", progress=0)
    note = MeetingNote.objects.create(title="进度", meeting_date=date(2026, 9, 5), raw_text="联调 20%")
    draft = ImportDraft.objects.create(meeting_note=note, payload={
        "tasks": [{"title": "接口联调", "project_name": project.name, "progress": 20}],
        "risks": [], "milestones": [],
    })
    html = admin_client.get(f"/drafts/{draft.pk}/").content.decode()
    assert "进度：0 → 20" in html
    assert f'data-recommended-existing="{task.pk}"' in html


def test_native_javascript_workflow_behaviors():
    result = subprocess.run(
        ["node", "--test", "tests/js/app.test.cjs"], cwd=PROJECT_ROOT,
        text=True, capture_output=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.django_db
def test_date_controls_render_browser_readable_initial_values(admin_client):
    project = Project.objects.create(name="日期项目")
    task = Task.objects.create(project=project, title="日期任务", due_date=date(2026, 9, 12))
    for path, field in (("/meetings/new/", "meeting_date"),
                        (f"/tasks/{task.pk}/", "due_date"),
                        (f"/tasks/{task.pk}/edit/", "due_date")):
        html = admin_client.get(path).content.decode()
        control = re.search(rf'<input[^>]*name="{field}"[^>]*>', html).group()
        assert re.search(r'value="\d{4}-\d{2}-\d{2}"', control), control
