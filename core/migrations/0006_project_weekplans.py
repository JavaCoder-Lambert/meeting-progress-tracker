from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("core", "0005_manual_meetings")]

    operations = [
        migrations.CreateModel(name="ProjectWeekPlan", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("week_start", models.DateField()),
            ("revision", models.PositiveIntegerField(default=1)),
            ("version", models.PositiveIntegerField(default=0)),
            ("status", models.CharField(choices=[("draft", "草稿"), ("published", "已发布")], default="draft", max_length=16)),
            ("reason", models.TextField(blank=True)),
            ("published_at", models.DateTimeField(blank=True, null=True)),
            ("project", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="weekplans", to="core.project")),
            ("source", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="copies", to="core.projectweekplan")),
        ], options={"ordering": ["-week_start", "-revision"], "indexes": [models.Index(fields=["project", "week_start"], name="weekplan_project_week")], "constraints": [models.UniqueConstraint(fields=("project", "week_start", "revision"), name="weekplan_revision_unique"), models.UniqueConstraint(condition=models.Q(status="draft"), fields=("project", "week_start"), name="weekplan_one_draft")]}),
        migrations.CreateModel(name="ProjectWeekPlanItem", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("delivery", models.TextField(blank=True)),
            ("snapshot", models.JSONField(default=dict)),
            ("plan", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="items", to="core.projectweekplan")),
            ("task", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="weekplan_items", to="core.task")),
        ], options={"ordering": ["pk"], "constraints": [models.UniqueConstraint(fields=("plan", "task"), name="weekplan_item_unique")]}),
    ]
