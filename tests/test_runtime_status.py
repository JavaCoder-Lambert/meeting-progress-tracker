from datetime import timedelta
import hashlib
import json
import sqlite3
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.db import OperationalError
from django.test import Client
from django.utils import timezone

from core.models import MeetingNote, ParseJob

pytestmark = pytest.mark.django_db


def test_readiness_checks_database_but_does_not_disclose_error(client):
    assert client.get("/ready/").status_code == 200
    with patch("core.services.runtime_status.connection.cursor", side_effect=OperationalError("private-database-path")):
        response = client.get("/ready/")
    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}
    assert "private" not in response.content.decode()


def test_settings_show_queue_age_and_configuration_without_making_model_requests(admin_client, settings, tmp_path):
    settings.DATA_DIR = tmp_path
    settings.APP_VERSION = "release-8f32abc"
    settings.LLM_API_KEY = "secret-api-key"
    settings.LLM_MODEL = "configured-model"
    note = MeetingNote.objects.create(title="联调周会", meeting_date=timezone.localdate(), raw_text="内容")
    ParseJob.objects.create(meeting_note=note, queued_at=timezone.now() - timedelta(minutes=5))
    with patch("httpx.Client.send", side_effect=AssertionError("Settings must not call models")):
        response = admin_client.get("/settings/")
    html = response.content.decode()
    assert "release-8f32abc" in html
    assert "未测试连接" in html
    assert "secret-api-key" not in html
    assert response.context["runtime"]["queued"] == 1
    assert response.context["runtime"]["oldest_wait_seconds"] >= 300
    assert response.context["runtime"]["backup"] is None
    assert "no-store" in response.headers["Cache-Control"]


def test_backup_records_last_verified_snapshot_without_secrets(settings, tmp_path):
    source = tmp_path / "source.sqlite3"
    with sqlite3.connect(source) as db:
        db.execute("CREATE TABLE example (value TEXT)")
        db.execute("INSERT INTO example VALUES ('kept')")
    settings.DATA_DIR = tmp_path
    with patch.dict(settings.DATABASES["default"], NAME=source):
        call_command("backup_db")
    marker = tmp_path / "backups" / ".last-backup.json"
    assert marker.exists()
    status = json.loads(marker.read_text())
    snapshot = tmp_path / "backups" / status["filename"]
    assert status["sha256"] == hashlib.sha256(snapshot.read_bytes()).hexdigest()
    assert status["completed_at"]
    assert marker.stat().st_mode & 0o777 == 0o600


def test_invalid_backup_marker_does_not_break_settings_or_echo_payload(admin_client, settings, tmp_path):
    settings.DATA_DIR = tmp_path
    (tmp_path / "backups").mkdir()
    (tmp_path / "backups" / ".last-backup.json").write_text('{"filename":"../../secret"}')
    response = admin_client.get("/settings/")
    assert response.status_code == 200
    assert response.context["runtime"]["backup"] is None
    assert "../../secret" not in response.content.decode()


def test_help_and_download_are_authenticated_and_restrict_document_names(admin_client, client):
    client = Client()
    assert client.get("/help/").status_code == 302
    assert client.get("/help/user-guide/download/").status_code == 302
    response = admin_client.get("/help/")
    assert response.status_code == 200
    assert "/help/user-guide/download/" in response.content.decode()
    download = admin_client.get("/help/user-guide/download/")
    assert download.status_code == 200
    assert 'attachment; filename="user-guide.md"' == download.headers["Content-Disposition"]
    assert "会议" in b"".join(download.streaming_content).decode()
    assert admin_client.get("/help/not-allowed/download/").status_code == 404
