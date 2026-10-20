from celery import shared_task
from django.utils import timezone

from .models import DownloadBatch, InvoiceSearch
from .services import run_download_batch, run_search


@shared_task(bind=True, autoretry_for=(ConnectionError,), retry_backoff=True, max_retries=3)
def search_invoices(self, search_id: int):
    search = InvoiceSearch.objects.get(pk=search_id)
    search.status = InvoiceSearch.Status.RUNNING
    search.error = ""
    search.save(update_fields=["status", "error"])
    try:
        run_search(search)
    except Exception as exc:
        search.status = InvoiceSearch.Status.FAILED
        search.error = str(exc)
        search.completed_at = timezone.now()
        search.save(update_fields=["status", "error", "completed_at"])
        raise


@shared_task(bind=True, queue="portal")
def download_invoices(self, batch_id: int):
    batch = DownloadBatch.objects.get(pk=batch_id)
    run_download_batch(batch)

