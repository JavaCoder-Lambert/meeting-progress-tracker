"""Append-only meeting audit records. Registered from core.models."""
from django.db import models
from django.utils import timezone


class RiskFollowup(models.Model):
    risk = models.ForeignKey("core.Risk", on_delete=models.PROTECT, related_name="followups")
    meeting_note = models.ForeignKey("core.MeetingNote", on_delete=models.PROTECT, related_name="risk_followups")
    occurred_on = models.DateField(db_index=True)
    before = models.JSONField(default=dict)
    after = models.JSONField(default=dict)
    response = models.TextField()
    next_step = models.TextField(blank=True)
    reason = models.TextField(blank=True)
    applied_to_risk = models.BooleanField(default=False)
    idempotency_key = models.CharField(max_length=100, unique=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-occurred_on", "-pk"]


class MeetingCorrection(models.Model):
    meeting_note = models.ForeignKey("core.MeetingNote", on_delete=models.PROTECT, related_name="corrections")
    progress_update = models.ForeignKey("core.ProgressUpdate", on_delete=models.PROTECT, related_name="corrections")
    reason = models.TextField()
    original = models.JSONField(default=dict)
    current = models.JSONField(default=dict)
    proposed = models.JSONField(default=dict)
    applied_to_task = models.BooleanField(default=False)
    idempotency_key = models.CharField(max_length=100, unique=True)
    confirmed_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-pk"]
