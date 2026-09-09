from datetime import datetime, timezone
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core.services.database_backup import snapshot_database
from core.services.runtime_status import record_backup_status


class Command(BaseCommand):
    help = "在线备份 SQLite 数据库并校验完整性；不会覆盖已有文件。"
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument("output", nargs="?", help="备份目标；默认 DATA_DIR/backups/时间戳.sqlite3")

    def handle(self, *args, **options):
        database = settings.DATABASES["default"]
        if database["ENGINE"] != "django.db.backends.sqlite3":
            raise CommandError("此命令仅支持 SQLite。")
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        output = options["output"] or Path(settings.DATA_DIR) / "backups" / f"tracker-{timestamp}.sqlite3"
        path, checksum = snapshot_database(database["NAME"], output)
        try:
            record_backup_status(path, checksum)
        except OSError:
            self.stderr.write("快照已成功，但最近备份状态未能更新；请保留下方路径和校验值。")
        self.stdout.write(self.style.SUCCESS(f"备份完成：{path}\nSHA256：{checksum}"))
