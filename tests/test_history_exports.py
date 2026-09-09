import csv
import io
import json
import zipfile

from django.utils import timezone

from core.models import Project, Task
from core.services.exports import build_csv_zip, build_json_export
from core.services.weekplans import create_draft, save_draft, publish_plan


def test_exports_include_new_plan_snapshots_and_history_collections(db):
    task = Task.objects.create(project=Project.objects.create(name="项目"), title="交付")
    plan = create_draft(task.project, timezone.localdate())
    save_draft(plan.pk, 0, {task.pk: "完成验收"})
    publish_plan(plan.pk, 1)
    payload = json.loads(build_json_export())
    assert payload["projectweekplan"][0]["status"] == "published"
    assert payload["projectweekplanitem"][0]["snapshot"]["title"] == "交付"
    assert payload["riskfollowup"] == []
    assert payload["meetingcorrection"] == []
    with zipfile.ZipFile(io.BytesIO(build_csv_zip())) as archive:
        rows = list(csv.DictReader(io.StringIO(archive.read("projectweekplanitem.csv").decode("utf-8-sig"))))
        assert rows[0]["delivery"] == "完成验收"
        assert "meetingcorrection.csv" in archive.namelist()
