Attempting to perform the InitializeDefaultDrives operation on the 'FileSystem' provider failed.
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from downloader.models import DownloadBatch, InvoiceCandidate, InvoiceSearch, Mailbox


class InterfaceRoutingTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("interface-tester", password="safe-password")
        Mailbox.objects.create(name="Contabilidad", address="contabilidad@example.com")
        self.client.force_login(self.user)

    def test_product_pages_render_for_authenticated_users(self):
        for route_name, heading in (("index", "Resumen"), ("search-page", "Nueva búsqueda"), ("library-page", "Descargas pendientes")):
            with self.subTest(route_name=route_name):
                response = self.client.get(reverse(route_name))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, heading)
                self.assertContains(response, "downloader/app.css")
                self.assertContains(response, "downloader/favicon.svg")
                self.assertContains(response, "AC-FacDownloader")

    def test_product_pages_require_authentication(self):
        self.client.logout()
        response = self.client.get(reverse("search-page"))
        self.assertRedirects(response, f"{reverse('login')}?next={reverse('search-page')}")

    def test_login_page_uses_the_product_shell(self):
        self.client.logout()
        response = self.client.get(reverse("login"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Tus facturas,")
        self.assertContains(response, "Workspace privado")
        self.assertContains(response, "downloader/favicon.svg")
        self.assertContains(response, "Albir Confort")

    def test_admin_has_a_return_link_to_the_product_panel(self):
        self.user.is_staff = True
        self.user.is_superuser = True
        self.user.save(update_fields=["is_staff", "is_superuser"])

        response = self.client.get(reverse("admin:index"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Volver al panel")
        self.assertContains(response, 'href="/"')
        self.assertContains(response, "ac-admin-dashboard")

    def test_download_status_exposes_folder_and_invoice_type(self):
        search = InvoiceSearch.objects.create(
            requested_by=self.user,
            mailbox=Mailbox.objects.get(address="contabilidad@example.com"),
            start_date="2026-10-01",
            end_date="2026-10-20",
            invoice_types=["agua"],
        )
        candidate = InvoiceCandidate.objects.create(
            search=search,
            message_id="message-1",
            provider="veolia",
            invoice_type="agua",
            sender="facturas@example.com",
            subject="Factura de agua",
        )
        batch = DownloadBatch.objects.create(requested_by=self.user, search=search)
        batch.items.create(candidate=candidate)

        response = self.client.get(reverse("download-status", args=[batch.pk]))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["folder_name"], f"facturas-{timezone.localtime(batch.created_at).date().isoformat()}")
        self.assertEqual(payload["items"][0]["invoice_type"], "agua")

