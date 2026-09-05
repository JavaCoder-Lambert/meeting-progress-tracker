from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core.services.database_backup import snapshot_database


class Command(BaseCommand):
    help = "将已校验的 SQLite 备份恢复到空目标。先停止 app/worker，或使用新数据卷。"
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument("backup", help="SQLite 备份文件")
        parser.add_argument("--target", help="目标数据库，默认当前 DATA_DIR/app.sqlite3；必须不存在")

    def handle(self, *args, **options):
        database = settings.DATABASES["default"]
        if database["ENGINE"] != "django.db.backends.sqlite3":
            raise CommandError("此命令仅支持 SQLite。")
        target = options["target"] or database["NAME"]
        path, checksum = snapshot_database(options["backup"], target)
        self.stdout.write(self.style.SUCCESS(f"恢复完成：{path}\nSHA256：{checksum}\n启动应用后将自动执行所需迁移。"))
