from datetime import date, timedelta

import pytest
from django.urls import reverse
from django.test import override_settings
from django.utils import timezone

from core.models import ImportDraft, MeetingNote, ParseJob


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
@override_settings(LLM_API_KEY="fake", LLM_MODEL="fake")
def test_capture_parse_intent_saves_and_enqueues_without_calling_model(admin_client, monkeypatch):
    def fail_if_called(_note):
        raise AssertionError("录入页不应同步解析")

    monkeypatch.setattr("core.services.parse_jobs.generate_meeting_payload", fail_if_called)

    response = admin_client.post(reverse("meeting_create"), {
        "title": "周会", "meeting_date": "2026-09-05", "raw_text": "会议原文", "intent": "parse",
    })

    note = MeetingNote.objects.get(title="周会")
    assert response.status_code == 302
    assert response.url == reverse('meeting_detail', args=[note.pk])
    assert note.parse_status == MeetingNote.ParseStatus.PARSING
    assert ParseJob.objects.get(meeting_note=note).status == "queued"


@pytest.mark.django_db
def test_failed_parse_hides_old_draft_and_offers_retry_in_inbox_detail_and_dashboard(admin_client):
    note = MeetingNote.objects.create(
        title="解析失败记录", meeting_date=date(2026, 9, 5), raw_text="原文",
        parse_status=MeetingNote.ParseStatus.FAILED,
    )
    old_draft = ImportDraft.objects.create(meeting_note=note, payload={})

    inbox = admin_client.get(reverse("meeting_list"))
    detail = admin_client.get(reverse("meeting_detail", args=[note.pk]))
    dashboard = admin_client.get(reverse("dashboard"))

    assert "重试解析" in inbox.content.decode()
    assert reverse("draft_review", args=[old_draft.pk]) not in inbox.content.decode()
    assert "已有解析草稿" not in detail.content.decode()
    assert list(dashboard.context["pending_drafts"]) == []
