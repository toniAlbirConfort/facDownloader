import hashlib
import re
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, time, timedelta, timezone as dt_timezone
from pathlib import Path
from urllib.parse import urlparse
from zipfile import ZIP_DEFLATED, ZipFile

from django.conf import settings
from django.db import close_old_connections
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .graph import DownloadedFile, GraphError, download_url
from .imap import ImapClient
from .models import AuditEvent, DownloadBatch, DownloadItem, InvoiceDocument, InvoiceSearch
from .providers import MailMessage, provider_for
from .portal import download_repsol_invoice

DOWNLOADABLE_INVOICE_TYPES = frozenset({"agua", "luz", "gas"})


def date_range_iso(search: InvoiceSearch) -> tuple[str, str]:
    start = datetime.combine(search.start_date, time.min, tzinfo=timezone.get_current_timezone()).astimezone(dt_timezone.utc)
    end = datetime.combine(search.end_date + timedelta(days=1), time.min, tzinfo=timezone.get_current_timezone()).astimezone(dt_timezone.utc)
    return start.isoformat().replace("+00:00", "Z"), end.isoformat().replace("+00:00", "Z")


def run_search(search: InvoiceSearch):
    total = 0
    folders = search.folders or [search.mailbox.imap_credential.folder or "INBOX"]
    with ImapClient(search.mailbox, auto_select=False) as client:
        for folder in folders:
            client.select_folder(folder)
            messages = client.list_messages(search.start_date, search.end_date)
            for invoice_type in search.invoice_types:
                provider = provider_for(invoice_type)
                for message in messages:
                    if not provider.matches(message):
                        continue
                    candidate_data = provider.extract_candidates(message)
                    received_at = parse_datetime(message.received_at) if message.received_at else None
                    _, created = search.candidates.get_or_create(
                        folder=folder,
                        message_id=message.id,
                        provider=provider.key,
                        defaults={
                            "internet_message_id": message.internet_message_id,
                            "invoice_type": invoice_type,
                            "sender": message.sender,
                            "subject": message.subject,
                            "received_at": received_at,
                            "source_links": candidate_data.links,
                            "metadata": candidate_data.metadata,
                        },
                    )
                    total += int(created)
    search.total_found = total
    search.status = InvoiceSearch.Status.COMPLETED
    search.completed_at = timezone.now()
    search.save(update_fields=["total_found", "status", "completed_at"])


def safe_filename(name: str, fallback: str = "factura.pdf") -> str:
    name = Path(name or fallback).name
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")
    if not name.lower().endswith(".pdf"):
        name += ".pdf"
    return name[:180] or fallback


def remove_document_file(document: InvoiceDocument):
    """Remove the temporary PDF and its metadata after delivery."""
    path = Path(document.file_path) if document.file_path else None
    document.delete()
    if path:
        path.unlink(missing_ok=True)


def cleanup_batch_artifacts(batch: DownloadBatch):
    """Release PDFs and the ZIP once a batch has been sent to the user."""
    documents = {
        item.document_id: item.document
        for item in batch.items.select_related("document")
        if item.document_id and item.document
    }
    for document in documents.values():
        other_batch_reference = DownloadItem.objects.filter(document=document).exclude(batch=batch).exists()
        if not other_batch_reference:
            remove_document_file(document)

    zip_path = Path(batch.zip_path) if batch.zip_path else None
    if zip_path:
        zip_path.unlink(missing_ok=True)
    if batch.zip_path:
        batch.zip_path = ""
        batch.save(update_fields=["zip_path"])


def download_folder_name(batch: DownloadBatch) -> str:
    created_at = batch.created_at
    if timezone.is_naive(created_at):
        created_at = timezone.make_aware(created_at, timezone.get_current_timezone())
    return f"facturas-{timezone.localtime(created_at).date().isoformat()}"


def download_archive_member_name(item: DownloadItem, use_type_folders: bool, root_name: str = "", filename: str | None = None) -> str:
    filename = safe_filename(filename or item.document.file_name)
    if not use_type_folders:
        member_name = filename
    else:
        invoice_type = item.document.invoice_type
        if invoice_type not in DOWNLOADABLE_INVOICE_TYPES:
            raise GraphError("El tipo de factura no es válido para crear la carpeta de descarga")
        member_name = f"{invoice_type}/{filename}"
    return f"{root_name}/{member_name}" if root_name else member_name


def unique_download_filename(filename: str, used_names: set[str]) -> str:
    if filename not in used_names:
        used_names.add(filename)
        return filename
    stem, dot, extension = filename.rpartition(".")
    if not dot:
        stem, extension = filename, ""
    index = 2
    candidate = f"{stem}-{index}{dot}{extension}"
    while candidate in used_names:
        index += 1
        candidate = f"{stem}-{index}{dot}{extension}"
    used_names.add(candidate)
    return candidate


def persist_file(downloaded: DownloadedFile, candidate, source_name: str) -> InvoiceDocument:
    sha = hashlib.sha256()
    with downloaded.path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            sha.update(chunk)
    digest = sha.hexdigest()
    existing = InvoiceDocument.objects.filter(sha256=digest, status=InvoiceDocument.Status.DOWNLOADED).first()
    if existing:
        downloaded.path.unlink(missing_ok=True)
        return existing

    root = settings.DOWNLOAD_TEMP_ROOT / "documents" / candidate.provider / str(timezone.now().year)
    root.mkdir(parents=True, exist_ok=True)
    filename = safe_filename(source_name, f"{candidate.invoice_type}-{candidate.id}.pdf")
    destination = root / f"{digest[:12]}-{filename}"
    staging_destination = destination.with_suffix(destination.suffix + ".partial")
    shutil.copyfile(downloaded.path, staging_destination)
    staging_destination.replace(destination)
    downloaded.path.unlink(missing_ok=True)
    return InvoiceDocument.objects.create(
        candidate=candidate,
        provider=candidate.provider,
        invoice_type=candidate.invoice_type,
        file_name=filename,
        file_path=str(destination),
        sha256=digest,
        size_bytes=downloaded.size_bytes,
        status=InvoiceDocument.Status.DOWNLOADED,
    )


def is_pdf(downloaded: DownloadedFile) -> bool:
    if downloaded.content_type == "application/pdf":
        return True
    with downloaded.path.open("rb") as source:
        return source.read(5) == b"%PDF-"


def download_candidate(
    candidate,
    mailbox,
    message: MailMessage | None = None,
    imap_prefetched: bool = False,
) -> InvoiceDocument:
    provider = provider_for(candidate.invoice_type)

    # Direct provider links are independent of IMAP. Resolve them first so a
    # batch does not reopen the mailbox and refetch the date range per item.
    if candidate.source_links:
        if provider.key == "repsol":
            downloaded = download_repsol_invoice(candidate.source_links)
            if is_pdf(downloaded):
                return persist_file(downloaded, candidate, f"{candidate.invoice_type}-{candidate.id}.pdf")
            downloaded.path.unlink(missing_ok=True)
        else:
            for link in candidate.source_links:
                downloaded = download_url(link, allowed_hosts=provider.allowed_hosts)
                if is_pdf(downloaded):
                    return persist_file(downloaded, candidate, f"{candidate.invoice_type}-{candidate.id}.pdf")
                downloaded.path.unlink(missing_ok=True)

    if message is None and not imap_prefetched:
        with ImapClient(mailbox, folder=candidate.folder) as client:
            messages = client.list_messages(candidate.search.start_date, candidate.search.end_date)
            message = next((item for item in messages if item.id == candidate.message_id), None)

    if message:
        for attachment in message.attachments:
            if attachment.content_type == "application/pdf" or attachment.name.lower().endswith(".pdf"):
                downloaded = ImapClient.attachment_file(attachment)
                if is_pdf(downloaded):
                    return persist_file(downloaded, candidate, attachment.name)

    raise GraphError("El proveedor no entregó un PDF directo; requiere revisión manual, DNI, 2FA o CAPTCHA")


def _prefetch_imap_messages(batch: DownloadBatch, items: list[DownloadItem]):
    folders = {item.candidate.folder for item in items if not item.candidate.source_links}
    if not folders:
        return {}, {}

    messages_by_folder: dict[str, dict[str, MailMessage]] = {}
    errors_by_folder: dict[str, Exception] = {}
    try:
        with ImapClient(batch.search.mailbox, auto_select=False) as client:
            for folder in folders:
                try:
                    client.select_folder(folder)
                    messages_by_folder[folder] = {
                        message.id: message
                        for message in client.list_messages(batch.search.start_date, batch.search.end_date)
                    }
                except Exception as exc:
                    errors_by_folder[folder] = exc
    except Exception as exc:
        errors_by_folder = {folder: exc for folder in folders}
    return messages_by_folder, errors_by_folder


def _download_item(
    item: DownloadItem,
    mailbox,
    message: MailMessage | None = None,
    imap_prefetched: bool = False,
    manage_connection: bool = True,
):
    if manage_connection:
        close_old_connections()
    try:
        document = download_candidate(
            item.candidate,
            mailbox,
            message=message,
            imap_prefetched=imap_prefetched,
        )
        return item, document, ""
    except Exception as exc:
        return item, None, str(exc)
    finally:
        if manage_connection:
            close_old_connections()


def _save_download_result(item: DownloadItem, document: InvoiceDocument | None, error: str):
    if document:
        item.document = document
        item.status = "downloaded"
        item.error = ""
        item.save(update_fields=["document", "status", "error"])
    else:
        item.status = "needs_review"
        item.error = error
        item.save(update_fields=["status", "error"])


def run_download_batch(batch: DownloadBatch):
    batch.status = DownloadBatch.Status.RUNNING
    batch.save(update_fields=["status"])
    settings.DOWNLOAD_TEMP_ROOT.mkdir(parents=True, exist_ok=True)
    try:
        items = list(batch.items.select_related("candidate"))
        messages_by_folder, errors_by_folder = _prefetch_imap_messages(batch, items)
        direct_items = [
            item
            for item in items
            if item.candidate.source_links and item.candidate.provider != "repsol"
        ]
        serial_items = [item for item in items if item not in direct_items]

        concurrency = max(1, int(getattr(settings, "DOWNLOAD_HTTP_CONCURRENCY", 4)))
        with ThreadPoolExecutor(max_workers=min(concurrency, len(direct_items) or 1)) as executor:
            futures = {
                executor.submit(_download_item, item, batch.search.mailbox): item
                for item in direct_items
            }
            for future in as_completed(futures):
                item, document, error = future.result()
                _save_download_result(item, document, error)

        for item in serial_items:
            folder = item.candidate.folder
            if not item.candidate.source_links and folder in errors_by_folder:
                _save_download_result(item, None, str(errors_by_folder[folder]))
                continue
            message = messages_by_folder.get(folder, {}).get(item.candidate.message_id)
            item, document, error = _download_item(
                item,
                batch.search.mailbox,
                message=message,
                imap_prefetched=not bool(item.candidate.source_links),
                manage_connection=False,
            )
            _save_download_result(item, document, error)

        successful = list(batch.items.filter(status="downloaded").select_related("document"))
        if not successful:
            batch.status = DownloadBatch.Status.FAILED
            batch.error = "No se pudo descargar ningún documento del lote"
            batch.completed_at = timezone.now()
            batch.save(update_fields=["status", "error", "completed_at"])
            return
        invoice_types = {item.document.invoice_type for item in successful}
        use_type_folders = len(invoice_types) > 1
        used_archive_names: dict[str, set[str]] = {}
        zip_path = settings.DOWNLOAD_TEMP_ROOT / f"batch-{batch.pk}.zip"
        with ZipFile(zip_path, "w", ZIP_DEFLATED) as archive:
            for item in successful:
                folder_key = item.document.invoice_type if use_type_folders else "root"
                filename = unique_download_filename(
                    safe_filename(item.document.file_name),
                    used_archive_names.setdefault(folder_key, set()),
                )
                archive.write(
                    item.document.file_path,
                    arcname=download_archive_member_name(
                        item,
                        use_type_folders,
                        download_folder_name(batch),
                        filename,
                    ),
                )
        batch.zip_path = str(zip_path)
        batch.status = DownloadBatch.Status.COMPLETED
        batch.completed_at = timezone.now()
        batch.save(update_fields=["zip_path", "status", "completed_at"])
    except Exception as exc:
        batch.status = DownloadBatch.Status.FAILED
        batch.error = str(exc)
        batch.completed_at = timezone.now()
        batch.save(update_fields=["status", "error", "completed_at"])

