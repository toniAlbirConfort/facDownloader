from datetime import date, datetime, timezone as dt_timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from zipfile import ZipFile

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from downloader.models import DownloadBatch, DownloadItem, InvoiceCandidate, InvoiceDocument, InvoiceSearch, Mailbox
from django.utils import timezone

from downloader.services import date_range_iso, download_archive_member_name, download_folder_name, run_download_batch, safe_filename


class ServiceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("tester", password="safe-password")
        self.mailbox = Mailbox.objects.create(name="Contabilidad", address="contabilidad@example.com")

    def test_dates_are_converted_to_utc(self):
        search = InvoiceSearch.objects.create(requested_by=self.user, mailbox=self.mailbox, start_date=date(2026, 8, 1), end_date=date(2026, 8, 2), invoice_types=["gas"])
        start, end = date_range_iso(search)
        self.assertTrue(start.endswith("Z"))
        self.assertTrue(end.endswith("Z"))
        self.assertLess(start, end)
        self.assertTrue(end.startswith("2026-08-02T22:00:00"))

    def test_filename_cannot_escape_storage(self):
        self.assertEqual(safe_filename("../../factura.pdf"), "factura.pdf")
        self.assertEqual(safe_filename("factura final"), "factura_final.pdf")

    def test_download_folder_name_uses_local_date(self):
        search = InvoiceSearch.objects.create(
            requested_by=self.user,
            mailbox=self.mailbox,
            start_date=date(2026, 8, 1),
            end_date=date(2026, 8, 2),
            invoice_types=["gas"],
        )
        batch = DownloadBatch.objects.create(requested_by=self.user, search=search)
        batch.created_at = datetime(2026, 10, 20, 22, 30, tzinfo=dt_timezone.utc)
        batch.save(update_fields=["created_at"])

        self.assertEqual(download_folder_name(batch), "facturas-2026-10-21")

    def test_download_zip_replicates_single_and_multiple_type_folders(self):
        with TemporaryDirectory() as temporary_root:
            root = Path(temporary_root)
            with self.settings(DOWNLOAD_TEMP_ROOT=root / "downloads"):
                search = InvoiceSearch.objects.create(
                    requested_by=self.user,
                    mailbox=self.mailbox,
                    start_date=date(2026, 8, 1),
                    end_date=date(2026, 8, 2),
                    invoice_types=["agua", "gas"],
                )
                batch = DownloadBatch.objects.create(requested_by=self.user, search=search)
                source_paths = []
                documents = []
                for invoice_type, filename in (("agua", "agua.pdf"), ("gas", "gas.pdf")):
                    source_path = root / filename
                    source_path.write_bytes(b"%PDF-1.7 test")
                    source_paths.append(source_path)
                    candidate = InvoiceCandidate.objects.create(
                        search=search,
                        message_id=f"message-{invoice_type}",
                        provider="repsol" if invoice_type == "gas" else "veolia",
                        invoice_type=invoice_type,
                        sender="facturas@example.com",
                        subject=f"Factura {invoice_type}",
                    )
                    documents.append(InvoiceDocument.objects.create(
                        candidate=candidate,
                        provider=candidate.provider,
                        invoice_type=invoice_type,
                        file_name=filename,
                        file_path=str(source_path),
                        size_bytes=source_path.stat().st_size,
                        status=InvoiceDocument.Status.DOWNLOADED,
                    ))
                    DownloadItem.objects.create(batch=batch, candidate=candidate)

                with patch("downloader.services.download_candidate", side_effect=documents):
                    run_download_batch(batch)

                with ZipFile(batch.zip_path) as archive:
                    self.assertEqual(
                        set(archive.namelist()),
                        {"facturas-2026-10-20/agua/agua.pdf", "facturas-2026-10-20/gas/gas.pdf"},
                    )
                agua_item = batch.items.get(candidate__invoice_type="agua")
                self.assertEqual(
                    download_archive_member_name(agua_item, True, "facturas-2026-10-20"),
                    "facturas-2026-10-20/agua/agua.pdf",
                )
                self.assertEqual(
                    download_archive_member_name(agua_item, False, "facturas-2026-10-20"),
                    "facturas-2026-10-20/agua.pdf",
                )

