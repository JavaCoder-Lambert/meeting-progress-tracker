import hashlib
import os
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile

from django.core.management.base import CommandError


def snapshot_database(source, destination):
    """Publish a verified SQLite snapshot without replacing an existing file."""
    source = Path(source).expanduser().resolve()
    destination = Path(destination).expanduser().resolve()
    if not source.is_file():
        raise CommandError(f"源数据库不存在：{source}")
    if destination.exists():
        raise CommandError(f"目标已存在，未做任何覆盖：{destination}")
    temporary_path = None
    try:
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=".snapshot-", delete=False) as temporary:
            temporary_path = Path(temporary.name)
        # Read-only URI avoids silently creating a missing source. The backup API
        # includes committed WAL data even while the application keeps running.
        with closing(sqlite3.connect(f"{source.as_uri()}?mode=ro", uri=True, timeout=20)) as reader:
            with closing(sqlite3.connect(temporary_path)) as writer:
                reader.backup(writer, pages=256)
                if writer.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                    raise CommandError("SQLite 完整性校验失败，未生成目标数据库。")
                if not writer.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchone():
                    raise CommandError("SQLite 数据库中没有数据表，未生成目标数据库。")
        # Both files live on the same filesystem. link() is atomic and fails if
        # another process has created the destination since the initial check.
        os.link(temporary_path, destination)
    except (sqlite3.Error, OSError) as exc:
        raise CommandError(f"SQLite 快照失败：{exc}") from exc
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    with destination.open("rb") as backup_file:
        checksum = hashlib.file_digest(backup_file, "sha256").hexdigest()
    return destination, checksum
