from datetime import timedelta
from uuid import uuid4

from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from core.models import ImportDraft, MeetingNote, ParseJob
from .llm_client import LLMParseError, generate_meeting_payload, validate_llm_config


IMPORTED_ERROR = "该会议已入库，请查看已入库结果；如有新的进展，请新建会议记录。"
EXPIRED_ERROR = "上次解析意外中断，记录已保存。请点击重试重新开始。"


def _is_imported(note):
    return note.parse_status == MeetingNote.ParseStatus.IMPORTED or note.drafts.filter(confirmed_at__isnull=False).exists()


def enqueue_parse(note):
    recover_expired_jobs()
    with transaction.atomic():
        # The first statement is a write: SQLite cannot upgrade an old read
        # snapshot while another connection is writing. This also serializes
        # the initial OneToOne creation with confirmation and duplicate POSTs.
        MeetingNote.objects.filter(pk=note.pk).update(updated_at=F("updated_at"))
        current = MeetingNote.objects.get(pk=note.pk)
        if _is_imported(current):
            raise LLMParseError(IMPORTED_ERROR)
        validate_llm_config()
        job, created = ParseJob.objects.get_or_create(meeting_note=current)
        if not created and job.status in (ParseJob.Status.QUEUED, ParseJob.Status.RUNNING):
            return job
        now = timezone.now()
        ParseJob.objects.filter(pk=job.pk).update(
            status=ParseJob.Status.QUEUED, queued_at=now, started_at=None,
            finished_at=None, lease_expires_at=None, lease_token=None,
            error="", draft=None, updated_at=now,
        )
        MeetingNote.objects.filter(pk=note.pk).update(
            parse_status=MeetingNote.ParseStatus.PARSING, parse_error="", updated_at=now,
        )
        job.refresh_from_db()
        return job


def recover_expired_jobs():
    now = timezone.now()
    expired = ParseJob.objects.filter(status=ParseJob.Status.RUNNING, lease_expires_at__lte=now)
    recovered = 0
    for pk in expired.values_list("pk", flat=True):
        with transaction.atomic():
            changed = ParseJob.objects.filter(pk=pk, status=ParseJob.Status.RUNNING, lease_expires_at__lte=now).update(
                status=ParseJob.Status.FAILED, error=EXPIRED_ERROR, finished_at=now,
                lease_token=None, lease_expires_at=None, updated_at=now,
            )
            if changed:
                job = ParseJob.objects.get(pk=pk)
                MeetingNote.objects.filter(pk=job.meeting_note_id, parse_status=MeetingNote.ParseStatus.PARSING).update(
                    parse_status=MeetingNote.ParseStatus.FAILED, parse_error=EXPIRED_ERROR, updated_at=now,
                )
                recovered += 1
    return recovered


def claim_next_job():
    recover_expired_jobs()
    pk = ParseJob.objects.filter(status=ParseJob.Status.QUEUED).values_list("pk", flat=True).first()
    if pk is None:
        return None
    now = timezone.now()
    token = uuid4()
    claimed = ParseJob.objects.filter(pk=pk, status=ParseJob.Status.QUEUED).update(
        status=ParseJob.Status.RUNNING, started_at=now, attempts=F("attempts") + 1,
        lease_token=token, lease_expires_at=now + timedelta(seconds=settings.LLM_TIMEOUT_SECONDS + 30), updated_at=now,
    )
    return ParseJob.objects.select_related("meeting_note").get(pk=pk, lease_token=token) if claimed else None


def _owned_job(job):
    return ParseJob.objects.filter(
        pk=job.pk, status=ParseJob.Status.RUNNING, lease_token=job.lease_token,
        lease_expires_at__gt=timezone.now(),
    )


def finish_job(job, *, content="", payload=None, error=""):
    with transaction.atomic():
        now = timezone.now()
        if not _owned_job(job).update(updated_at=now):
            return False
        note = MeetingNote.objects.get(pk=job.meeting_note_id)
        if _is_imported(note):
            error = IMPORTED_ERROR
        draft = None
        if error:
            if note.parse_status != MeetingNote.ParseStatus.IMPORTED and not note.drafts.filter(confirmed_at__isnull=False).exists():
                MeetingNote.objects.filter(pk=note.pk).update(parse_status=MeetingNote.ParseStatus.FAILED, parse_error=error, updated_at=now)
        else:
            draft = ImportDraft.objects.create(meeting_note=note, payload=payload)
            MeetingNote.objects.filter(pk=note.pk).update(
                raw_llm_response=content, parse_status=MeetingNote.ParseStatus.SUCCESS, parse_error="", updated_at=now,
            )
        ParseJob.objects.filter(pk=job.pk).update(
            status=ParseJob.Status.FAILED if error else ParseJob.Status.SUCCESS,
            error=error, draft=draft, finished_at=now, lease_token=None,
            lease_expires_at=None, updated_at=now,
        )
        return True


def run_next_job():
    job = claim_next_job()
    if job is None:
        return False
    try:
        if _is_imported(job.meeting_note):
            raise LLMParseError(IMPORTED_ERROR)
        content, payload = generate_meeting_payload(job.meeting_note)
    except LLMParseError as exc:
        finish_job(job, error=str(exc))
    except Exception:
        # Never persist provider response objects, API keys, or tracebacks.
        finish_job(job, error="解析暂时未能完成，记录已保存。请稍后重试。")
    else:
        finish_job(job, content=content, payload=payload)
    return True
