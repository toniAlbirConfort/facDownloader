import json
from pathlib import Path

from django.contrib.auth.decorators import login_required
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import render
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from .graph import GraphError
from .imap import ImapClient
from .models import AuditEvent, DownloadBatch, DownloadItem, InvoiceDocument, InvoiceSearch, Mailbox
from .services import cleanup_batch_artifacts, download_folder_name, remove_document_file
from .tasks import download_invoices, search_invoices


def json_body(request):
    try:
        return json.loads(request.body or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError("JSON no válido") from exc


@login_required
def index(request):
    searches = InvoiceSearch.objects.filter(requested_by=request.user).select_related("mailbox", "requested_by")
    context = {
        "active_mailboxes": Mailbox.objects.filter(is_active=True),
        "recent_searches": searches[:6],
        "searches_count": searches.count(),
        "running_count": searches.filter(status__in=[InvoiceSearch.Status.QUEUED, InvoiceSearch.Status.RUNNING]).count(),
        "documents_count": InvoiceDocument.objects.filter(
            status=InvoiceDocument.Status.DOWNLOADED,
            **({"candidate__search__requested_by": request.user} if not request.user.is_staff else {}),
        ).count(),
    }
    return render(request, "downloader/dashboard.html", context)


@login_required
def search_page(request):
    return render(request, "downloader/search.html", {"mailboxes": Mailbox.objects.filter(is_active=True)})


@login_required
@require_GET
def mailbox_folders(request, mailbox_id):
    try:
        mailbox = Mailbox.objects.get(pk=mailbox_id, is_active=True)
        with ImapClient(mailbox, auto_select=False) as client:
            folders = client.list_folders()
        default_folder = mailbox.imap_credential.folder or "INBOX"
        return JsonResponse({"folders": folders, "default_folder": default_folder})
    except Mailbox.DoesNotExist as exc:
        return JsonResponse({"error": str(exc)}, status=404)
    except GraphError as exc:
        return JsonResponse({"error": str(exc)}, status=502)


@login_required
def library_page(request):
    return render(request, "downloader/library.html")


@login_required
@require_POST
def create_search(request):
    try:
        payload = json_body(request)
        start_date = parse_date(payload.get("start_date", ""))
        end_date = parse_date(payload.get("end_date", ""))
        invoice_types = sorted(set(payload.get("invoice_types", [])))
        if not start_date or not end_date or start_date > end_date:
            raise ValueError("El rango de fechas no es válido")
        if not invoice_types or not set(invoice_types).issubset({"gas", "luz", "agua"}):
            raise ValueError("Debe seleccionar tipos de factura válidos")
        raw_folders = payload.get("folders")
        if raw_folders is None:
            folders = []
        elif not isinstance(raw_folders, list):
            raise ValueError("Las carpetas IMAP no son válidas")
        else:
            folders = sorted({str(folder).strip() for folder in raw_folders if str(folder).strip()})
        if len(folders) > 50 or any(len(folder) > 255 for folder in folders):
            raise ValueError("Las carpetas IMAP no son válidas")
        mailbox = Mailbox.objects.get(pk=payload.get("mailbox_id"), is_active=True)
        search = InvoiceSearch.objects.create(
            requested_by=request.user,
            mailbox=mailbox,
            start_date=start_date,
            end_date=end_date,
            invoice_types=invoice_types,
            folders=folders,
        )
        AuditEvent.objects.create(actor=request.user, action="search_created", object_type="InvoiceSearch", object_id=search.pk)
        search_invoices.delay(search.pk)
        return JsonResponse({"id": search.pk, "status": search.status}, status=202)
    except (ValueError, Mailbox.DoesNotExist) as exc:
        return JsonResponse({"error": str(exc)}, status=400)


def serialize_search(search):
    return {
        "id": search.pk,
        "status": search.status,
        "error": search.error,
        "total_found": search.total_found,
        "mailbox": search.mailbox.address,
        "folders": search.folders,
        "start_date": search.start_date.isoformat(),
        "end_date": search.end_date.isoformat(),
    }


@login_required
@require_GET
def search_detail(request, search_id):
    try:
        search = InvoiceSearch.objects.get(pk=search_id, requested_by=request.user)
    except InvoiceSearch.DoesNotExist as exc:
        raise Http404 from exc
    return JsonResponse(serialize_search(search))


@login_required
@require_GET
def search_results(request, search_id):
    try:
        search = InvoiceSearch.objects.get(pk=search_id, requested_by=request.user)
    except InvoiceSearch.DoesNotExist as exc:
        raise Http404 from exc
    results = [{
        "id": item.pk,
        "provider": item.provider,
        "invoice_type": item.invoice_type,
        "sender": item.sender,
        "subject": item.subject,
        "received_at": item.received_at.isoformat() if item.received_at else None,
        "links": len(item.source_links),
    } for item in search.candidates.all()]
    return JsonResponse({"results": results})


@login_required
@require_POST
def start_download(request, search_id):
    try:
        search = InvoiceSearch.objects.get(pk=search_id, requested_by=request.user)
    except InvoiceSearch.DoesNotExist as exc:
        raise Http404 from exc
    payload = json_body(request)
    selected = payload.get("candidate_ids")
    candidates = search.candidates.filter(pk__in=selected) if selected else search.candidates.all()
    if not candidates.exists():
        return JsonResponse({"error": "No hay resultados seleccionados"}, status=400)
    batch = DownloadBatch.objects.create(requested_by=request.user, search=search)
    DownloadItem.objects.bulk_create([DownloadItem(batch=batch, candidate=candidate) for candidate in candidates])
    AuditEvent.objects.create(actor=request.user, action="download_started", object_type="DownloadBatch", object_id=batch.pk)
    download_invoices.delay(batch.pk)
    return JsonResponse({"id": batch.pk, "status": batch.status}, status=202)


@login_required
@require_GET
def download_status(request, batch_id):
    try:
        batch = DownloadBatch.objects.get(pk=batch_id, requested_by=request.user)
    except DownloadBatch.DoesNotExist as exc:
        raise Http404 from exc
    items = []
    for item in batch.items.select_related("candidate", "document"):
        item_data = {
            "candidate_id": item.candidate_id,
            "invoice_type": item.document.invoice_type if item.document else item.candidate.invoice_type,
            "status": item.status,
            "error": item.error,
        }
        if item.document and item.status == "downloaded":
            item_data.update({
                "document_id": item.document_id,
                "file_name": item.document.file_name,
                "download_url": f"/api/documents/{item.document_id}/download",
            })
        items.append(item_data)

    return JsonResponse({
        "id": batch.pk,
        "status": batch.status,
        "error": batch.error,
        "folder_name": download_folder_name(batch),
        "zip_available": bool(batch.zip_path and Path(batch.zip_path).exists()),
        "items": items,
    })


@login_required
@require_GET
def download_zip(request, batch_id):
    try:
        batch = DownloadBatch.objects.get(pk=batch_id, requested_by=request.user)
    except DownloadBatch.DoesNotExist as exc:
        raise Http404 from exc
    if not batch.zip_path or not Path(batch.zip_path).is_file():
        raise Http404
    response = FileResponse(open(batch.zip_path, "rb"), as_attachment=True, filename=f"facturas-{batch.pk}.zip")
    response._resource_closers.append(lambda: cleanup_batch_artifacts(batch))
    return response


@login_required
@require_GET
def document_download(request, document_id):
    try:
        document = InvoiceDocument.objects.select_related("candidate__search").get(pk=document_id, status=InvoiceDocument.Status.DOWNLOADED)
    except InvoiceDocument.DoesNotExist as exc:
        raise Http404 from exc
    if not request.user.is_staff and (not document.candidate or document.candidate.search.requested_by_id != request.user.id):
        return JsonResponse({"error": "No autorizado"}, status=403)
    if not document.file_path or not Path(document.file_path).is_file():
        raise Http404
    AuditEvent.objects.create(actor=request.user, action="document_downloaded", object_type="InvoiceDocument", object_id=document.pk)
    response = FileResponse(open(document.file_path, "rb"), as_attachment=True, filename=document.file_name)
    response._resource_closers.append(lambda: remove_document_file(document))
    return response


@login_required
@require_GET
def document_list(request):
    documents = InvoiceDocument.objects.filter(status=InvoiceDocument.Status.DOWNLOADED, candidate__search__requested_by=request.user).select_related("candidate")
    if request.user.is_staff:
        documents = InvoiceDocument.objects.filter(status=InvoiceDocument.Status.DOWNLOADED).select_related("candidate")
    return JsonResponse({"documents": [{
        "id": document.pk,
        "file_name": document.file_name,
        "provider": document.provider,
        "invoice_type": document.invoice_type,
        "size_bytes": document.size_bytes,
        "created_at": document.created_at.isoformat(),
        "download_url": f"/api/documents/{document.pk}/download",
    } for document in documents]})


@login_required
@require_http_methods(["DELETE"])
def document_delete(request, document_id):
    if not request.user.is_staff:
        return JsonResponse({"error": "Se requiere administrador"}, status=403)
    try:
        document = InvoiceDocument.objects.get(pk=document_id)
    except InvoiceDocument.DoesNotExist as exc:
        raise Http404 from exc
    remove_document_file(document)
    AuditEvent.objects.create(actor=request.user, action="document_deleted", object_type="InvoiceDocument", object_id=document_id)
    return JsonResponse({"deleted": True})

