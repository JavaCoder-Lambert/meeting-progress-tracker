import pytest
from django.urls import reverse


@pytest.mark.django_db
def test_capture_page_exposes_account_scoped_browser_stash_and_honest_fallback(admin_client, admin_user):
    response = admin_client.get(reverse("meeting_create"))
    html = response.content.decode()
    assert "data-capture-draft" in html
    assert f'data-user-id="{admin_user.pk}"' in html
    assert 'data-bound="false"' in html
    assert 'name="capture_draft_token"' in html
    assert 'src="/static/js/capture-draft.js"' in html
    assert 'role="status"' in html
    assert "仅保存在当前浏览器" in html
    assert "不会同步到其他设备" in html
    assert "<noscript>" in html
    assert "未启用 JavaScript" in html
    assert 'type="button" data-capture-clear hidden' in html


@pytest.mark.django_db
def test_invalid_capture_submission_marks_bound_input_as_authoritative(admin_client):
    response = admin_client.post(reverse("meeting_create"), {
        "title": "", "meeting_date": "2026-09-06", "raw_text": "本次提交仍保留", "intent": "save",
    })
    assert response.status_code == 200
    html = response.content.decode()
    assert 'data-bound="true"' in html
    assert "本次提交仍保留" in html
