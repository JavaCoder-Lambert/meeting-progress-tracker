"""Exercise additive upgrades without ever migrating the caller's database."""
import os
from pathlib import Path
import subprocess
import sys


def test_existing_0005_meetings_and_drafts_survive_new_empty_history_tables(tmp_path):
    code = '''
import django
from django.conf import settings
settings.DATABASES["default"]["NAME"] = ":memory:"
django.setup()
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
executor = MigrationExecutor(connection)
executor.migrate([("core", "0005_manual_meetings")])
old = executor.loader.project_state([("core", "0005_manual_meetings")]).apps
project = old.get_model("core", "Project").objects.create(name="迁移验证项目", progress=70)
note = old.get_model("core", "MeetingNote").objects.create(title="未结束会议", meeting_date="2026-09-04", raw_text="保留原文")
session = old.get_model("core", "MeetingSession").objects.create(meeting_note=note, state={"agenda":"保留尚未确认的议题"}, version=8)
draft = old.get_model("core", "ImportDraft").objects.create(meeting_note=note, payload={"tasks":[]}, review_state={"payload":{"summary":"未确认"}}, review_version=3)
task = old.get_model("core", "Task").objects.create(project=project, title="原有任务", progress=40, planned_for="2026-09-05")
executor = MigrationExecutor(connection)
executor.migrate(executor.loader.graph.leaf_nodes())
from core.models import Project, MeetingNote, MeetingSession, ImportDraft, Task, ProjectWeekPlan, ProjectWeekPlanItem, RiskFollowup, MeetingCorrection
assert Project.objects.get(pk=project.pk).progress == 70
assert MeetingNote.objects.get(pk=note.pk).raw_text == "保留原文"
assert MeetingSession.objects.get(pk=session.pk).state["agenda"] == "保留尚未确认的议题"
assert MeetingSession.objects.get(pk=session.pk).version == 8
assert ImportDraft.objects.get(pk=draft.pk).review_version == 3
assert ImportDraft.objects.get(pk=draft.pk).review_state["payload"]["summary"] == "未确认"
assert Task.objects.get(pk=task.pk).planned_for.isoformat() == "2026-09-05"
assert all(model.objects.count() == 0 for model in (ProjectWeekPlan, ProjectWeekPlanItem, RiskFollowup, MeetingCorrection))
assert connection.cursor().execute("PRAGMA integrity_check").fetchone() == ("ok",)
print("0005 -> latest: existing data preserved; 4 history tables empty; integrity ok")
'''
    result = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1],
        env={"PATH": os.environ.get("PATH", ""), "DJANGO_SETTINGS_MODULE": "tracker.settings",
             "DJANGO_DEBUG": "true", "DATA_DIR": str(tmp_path)}, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert "existing data preserved" in result.stdout
