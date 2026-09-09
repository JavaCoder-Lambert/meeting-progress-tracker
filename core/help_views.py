from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from .services.runtime_status import database_ready


DOCUMENTS = {
    "user-guide": ("日常使用指南", "user-guide.md"),
    "migration": ("带数据迁移到服务器", "migration.md"),
    "operations": ("升级、备份与恢复", "operations.md"),
    "troubleshooting": ("故障排查", "troubleshooting.md"),
    "deployment": ("全新服务器部署", "deployment.md"),
}


@require_GET
@never_cache
def readiness(request):
    ready = database_ready()
    return JsonResponse({"status": "ready" if ready else "unavailable"}, status=200 if ready else 503)


@login_required
@require_GET
@never_cache
def help_index(request):
    return render(request, "core/help.html", {"documents": DOCUMENTS.items()})


@login_required
@require_GET
@never_cache
def document_download(request, slug):
    if slug not in DOCUMENTS:
        raise Http404
    filename = DOCUMENTS[slug][1]
    try:
        document = (settings.BASE_DIR / "docs" / filename).open("rb")
    except FileNotFoundError as exc:
        raise Http404 from exc
    return FileResponse(document, as_attachment=True, filename=filename, content_type="text/markdown; charset=utf-8")
