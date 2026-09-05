import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0001_initial")]

    operations = [
        migrations.CreateModel(
            name="ParseJob",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("status", models.CharField(choices=[("queued", "等待解析"), ("running", "解析中"), ("success", "解析完成"), ("failed", "解析失败")], db_index=True, default="queued", max_length=16)),
                ("queued_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
                ("lease_expires_at", models.DateTimeField(blank=True, null=True)),
                ("lease_token", models.UUIDField(blank=True, null=True)),
                ("attempts", models.PositiveIntegerField(default=0)),
                ("error", models.TextField(blank=True)),
                ("meeting_note", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="parse_job", to="core.meetingnote")),
                ("draft", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="parse_jobs", to="core.importdraft")),
            ],
            options={"ordering": ["queued_at", "pk"]},
        ),
    ]
