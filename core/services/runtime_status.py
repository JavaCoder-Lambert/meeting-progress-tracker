"""Read-only operational facts. Never call a model from a status page."""
from datetime import datetime
import json
import os
from pathlib import Path
import re
import tempfile

from django.conf import settings
from django.db import DatabaseError, connection
from django.db.models import Count, Max, Min, Q
from django.utils import timezone

from core.models import ParseJob


def database_ready():
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT version FROM core_meetingsession LIMIT 1")
            cursor.execute("SELECT status FROM core_parsejob LIMIT 1")
        return True
    except DatabaseError:
        return False


def record_backup_status(path, checksum):
    directory = Path(settings.DATA_DIR) / "backups"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=directory, prefix=".backup-status-", delete=False) as handle:
            temporary = Path(handle.name)
            json.dump({"completed_at": timezone.now().isoformat(), "filename": Path(path).name,
                       "sha256": checksum}, handle)
        os.replace(temporary, directory / ".last-backup.json")
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def last_backup():
    try:
        path = Path(settings.DATA_DIR) / "backups" / ".last-backup.json"
        if path.stat().st_size > 4096:
            return None
        value = json.loads(path.read_text())
        if not isinstance(value, dict):
            return None
        filename = value.get("filename", "")
        if not isinstance(filename, str) or not re.fullmatch(r"[\w.-]+\.sqlite3", filename):
            return None
        checksum = value.get("sha256", "")
        if not isinstance(checksum, str) or not re.fullmatch(r"[0-9a-f]{64}", checksum):
            return None
        completed = datetime.fromisoformat(value["completed_at"])
        if timezone.is_naive(completed) or completed > timezone.now():
            return None
        return {"filename": filename, "completed_at": completed, "sha256": checksum}
    except (OSError, ValueError, TypeError, KeyError):
        return None


def runtime_status():
    now = timezone.now()
    stats = ParseJob.objects.aggregate(
        queued=Count("pk", filter=Q(status=ParseJob.Status.QUEUED)),
        running=Count("pk", filter=Q(status=ParseJob.Status.RUNNING)),
        failed=Count("pk", filter=Q(status=ParseJob.Status.FAILED)),
        oldest_queued_at=Min("queued_at", filter=Q(status=ParseJob.Status.QUEUED)),
        last_finished_at=Max("finished_at"),
    )
    age = max(0, int((now - stats["oldest_queued_at"]).total_seconds())) if stats["oldest_queued_at"] else None
    return {**stats, "version": settings.APP_VERSION, "database_ready": database_ready(),
            "oldest_wait_seconds": age, "queue_warning": age is not None and age > settings.LLM_TIMEOUT_SECONDS + 60,
            "backup": last_backup()}
