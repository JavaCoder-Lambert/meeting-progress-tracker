import csv
import io
import json
import zipfile
from datetime import date, datetime

from django.forms.models import model_to_dict

from core.models import (MeetingCorrection, MeetingNote, MeetingSession, Milestone, Person, ProgressUpdate,
                         Project, ProjectPhase, ProjectWeekPlan, ProjectWeekPlanItem, Risk, RiskFollowup, Task)

MODELS = [Project, ProjectPhase, Person, MeetingNote, MeetingSession, Task, ProgressUpdate, Risk, Milestone,
          ProjectWeekPlan, ProjectWeekPlanItem, RiskFollowup, MeetingCorrection]


def _json_value(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def build_json_export() -> bytes:
    payload = {}
    for model in MODELS:
        payload[model._meta.model_name] = [{key: _json_value(value) for key, value in model_to_dict(row).items()} for row in model.objects.all()]
    return json.dumps(payload, ensure_ascii=False, indent=2).encode()


def build_csv_zip() -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for model in MODELS:
            fields = [field.name for field in model._meta.fields]
            text = io.StringIO()
            writer = csv.writer(text)
            writer.writerow(fields)
            for row in model.objects.all():
                writer.writerow([_json_value(getattr(row, field.attname)) for field in model._meta.fields])
            archive.writestr(f"{model._meta.model_name}.csv", "\ufeff" + text.getvalue())
    return output.getvalue()
