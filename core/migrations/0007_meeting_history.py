import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0006_project_weekplans")]
    operations = [
        migrations.CreateModel(name="RiskFollowup", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("occurred_on", models.DateField(db_index=True)),
            ("before", models.JSONField(default=dict)), ("after", models.JSONField(default=dict)),
            ("response", models.TextField()), ("next_step", models.TextField(blank=True)),
            ("reason", models.TextField(blank=True)), ("applied_to_risk", models.BooleanField(default=False)),
            ("idempotency_key", models.CharField(max_length=100, unique=True)),
            ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
            ("risk", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="followups", to="core.risk")),
            ("meeting_note", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="risk_followups", to="core.meetingnote")),
        ], options={"ordering": ["-occurred_on", "-pk"]}),
        migrations.CreateModel(name="MeetingCorrection", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("reason", models.TextField()), ("original", models.JSONField(default=dict)),
            ("current", models.JSONField(default=dict)), ("proposed", models.JSONField(default=dict)),
            ("applied_to_task", models.BooleanField(default=False)),
            ("idempotency_key", models.CharField(max_length=100, unique=True)),
            ("confirmed_at", models.DateTimeField(default=django.utils.timezone.now)),
            ("meeting_note", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="corrections", to="core.meetingnote")),
            ("progress_update", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="corrections", to="core.progressupdate")),
        ], options={"ordering": ["-pk"]}),
    ]
