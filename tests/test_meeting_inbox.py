from datetime import date, timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from core.models import ImportDraft, MeetingNote


@pytest.mark.django_db
def test_meeting_inbox_shows_state_specific_next_actions_and_uses_only_latest_draft(admin_client):
    raw = MeetingNote.objects.create(
        title="待解析记录", meeting_date=date(2026, 9, 5), raw_text="原文",
        parse_status=MeetingNote.ParseStatus.NOT_PARSED,
    )
    failed = MeetingNote.objects.create(
        title="解析失败记录", meeting_date=date(2026, 9, 4), raw_text="原文",
        parse_status=MeetingNote.ParseStatus.FAILED,
    )
    reviewed = MeetingNote.objects.create(
        title="待确认记录", meeting_date=date(2026, 9, 3), raw_text="原文",
        parse_status=MeetingNote.ParseStatus.SUCCESS,
    )
    old_draft = ImportDraft.objects.create(meeting_note=reviewed, payload={})
    latest_draft = ImportDraft.objects.create(meeting_note=reviewed, payload={})
    ImportDraft.objects.filter(pk=old_draft.pk).update(created_at=timezone.now() - timedelta(minutes=1))
    imported = MeetingNote.objects.create(
        title="已入库记录", meeting_date=date(2026, 9, 2), raw_text="原文",
        parse_status=MeetingNote.ParseStatus.IMPORTED,
    )

    response = admin_client.get(reverse("meeting_list"))

    assert response.status_code == 200
    assert [note.pk for note in response.context["meeting_list"]] == [raw.pk, failed.pk, reviewed.pk, imported.pk]
    content = response.content.decode()
    assert "开始解析" in content
    assert "重试解析" in content
    assert reverse("draft_review", args=[latest_draft.pk]) in content
    assert reverse("draft_review", args=[old_draft.pk]) not in content
    assert "查看结果" in content


@pytest.mark.django_db
def test_capture_parse_intent_saves_then_redirects_to_auto_parse_flag_without_calling_model(admin_client, monkeypatch):
    def fail_if_called(_note):
        raise AssertionError("录入页不应同步解析")

    monkeypatch.setattr("core.views.parse_meeting_note", fail_if_called)

    response = admin_client.post(reverse("meeting_create"), {
        "title": "周会", "meeting_date": "2026-09-05", "raw_text": "会议原文", "intent": "parse",
    })

    note = MeetingNote.objects.get(title="周会")
    assert response.status_code == 302
    assert response.url == f"{reverse('meeting_detail', args=[note.pk])}?auto_parse=1"
    assert note.parse_status == MeetingNote.ParseStatus.NOT_PARSED
