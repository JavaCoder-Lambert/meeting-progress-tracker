import threading
import time
import signal
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections

from core.services.parse_jobs import run_next_job


HEARTBEAT_PATH = Path("/tmp/tracker-worker-heartbeat")


class Command(BaseCommand):
    help = "处理持久化会议解析队列。每个部署只需启动一个 worker。"

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="处理最多一个待解析任务后退出")
        parser.add_argument("--check", action="store_true", help="检查独立 worker 的心跳")

    def handle(self, *args, **options):
        if options["check"]:
            try:
                age = time.time() - HEARTBEAT_PATH.stat().st_mtime
            except FileNotFoundError as exc:
                raise CommandError("worker 尚未启动") from exc
            if not 0 <= age <= 60:
                raise CommandError("worker 心跳已过期")
            self.stdout.write("worker healthy")
            return

        stop = threading.Event()
        previous_sigterm = signal.getsignal(signal.SIGTERM)
        signal.signal(signal.SIGTERM, lambda *_: stop.set())

        def heartbeat():
            while not stop.is_set():
                HEARTBEAT_PATH.touch()
                stop.wait(10)

        HEARTBEAT_PATH.touch()
        thread = threading.Thread(target=heartbeat, daemon=True, name="parse-worker-heartbeat")
        thread.start()
        try:
            while not stop.is_set():
                close_old_connections()
                worked = run_next_job()
                if options["once"]:
                    return
                if not worked:
                    stop.wait(2)
        except KeyboardInterrupt:
            pass
        finally:
            stop.set()
            thread.join(timeout=1)
            signal.signal(signal.SIGTERM, previous_sigterm)
            close_old_connections()
