import hashlib
import sqlite3
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError


def create_database(path):
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE notes (title TEXT)")
        connection.execute("INSERT INTO notes VALUES ('项目计划')")


def test_online_backup_restores_committed_data_and_reports_checksum(tmp_path, settings, monkeypatch):
    source = tmp_path / "app.sqlite3"
    create_database(source)
    monkeypatch.setitem(settings.DATABASES["default"], "NAME", source)
    backup = tmp_path / "backups" / "snapshot.sqlite3"
    output = StringIO()

    with sqlite3.connect(source) as active_connection:
        active_connection.execute("INSERT INTO notes VALUES ('待提交')")
        call_command("backup_db", str(backup), stdout=output)
        active_connection.rollback()

    assert hashlib.sha256(backup.read_bytes()).hexdigest() in output.getvalue()
    assert backup.stat().st_mode & 0o777 == 0o600
    restored = tmp_path / "restored" / "app.sqlite3"
    call_command("restore_db", str(backup), target=str(restored))
    with sqlite3.connect(restored) as connection:
        assert connection.execute("SELECT title FROM notes").fetchall() == [("项目计划",)]
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)


def test_backup_refuses_existing_destination_and_leaves_it_unchanged(tmp_path, settings, monkeypatch):
    source = tmp_path / "app.sqlite3"
    create_database(source)
    monkeypatch.setitem(settings.DATABASES["default"], "NAME", source)
    with pytest.raises(CommandError, match="已存在"):
        call_command("backup_db", str(source))
    with sqlite3.connect(source) as connection:
        assert connection.execute("SELECT title FROM notes").fetchone() == ("项目计划",)


def test_restore_refuses_existing_database(tmp_path):
    source = tmp_path / "snapshot.sqlite3"
    destination = tmp_path / "app.sqlite3"
    create_database(source)
    destination.write_bytes(b"existing data")
    with pytest.raises(CommandError, match="已存在"):
        call_command("restore_db", str(source), target=str(destination))
    assert destination.read_bytes() == b"existing data"


def test_restore_uses_configured_empty_data_volume(tmp_path, settings, monkeypatch):
    source = tmp_path / "snapshot.sqlite3"
    destination = tmp_path / "new-volume" / "app.sqlite3"
    create_database(source)
    monkeypatch.setitem(settings.DATABASES["default"], "NAME", destination)
    call_command("restore_db", str(source))
    with sqlite3.connect(destination) as connection:
        assert connection.execute("SELECT title FROM notes").fetchone() == ("项目计划",)


def test_restore_rejects_invalid_database_without_creating_target(tmp_path):
    source = tmp_path / "not-a-backup.sqlite3"
    source.write_bytes(b"not a sqlite database")
    destination = tmp_path / "restored.sqlite3"
    with pytest.raises(CommandError, match="SQLite"):
        call_command("restore_db", str(source), target=str(destination))
    assert not destination.exists()


def test_backup_missing_source_does_not_create_an_empty_database(tmp_path, settings, monkeypatch):
    source = tmp_path / "missing.sqlite3"
    monkeypatch.setitem(settings.DATABASES["default"], "NAME", source)
    with pytest.raises(CommandError, match="不存在"):
        call_command("backup_db", str(tmp_path / "snapshot.sqlite3"))
    assert not source.exists()
