Attempting to perform the InitializeDefaultDrives operation on the 'FileSystem' provider failed.
from django.test import SimpleTestCase, override_settings

from downloader.providers import MailMessage, RepsolProvider, VeoliaProvider, extract_allowed_links


class ProviderTests(SimpleTestCase):
    def test_repsol_subject_and_sender_are_normalized(self):
        provider = RepsolProvider("gas")
        message = MailMessage("1", "<1>", "TUFACTURA@INFORMATIVO.REPSOLLUZYGAS.COM", "Tienes una nueva factura de GÁS.", None, "")
        self.assertTrue(provider.matches(message))

    @override_settings(INVOICE_TEST_SENDERS={"toni@albirconfort.es"})
    def test_configured_test_sender_is_accepted_with_provider_subject(self):
        provider = VeoliaProvider()
        message = MailMessage("1", "<1>", "toni@albirconfort.es", provider.subject, None, "")
        self.assertTrue(provider.matches(message))

    @override_settings(INVOICE_TEST_SENDERS={"toni@albirconfort.es"})
    def test_forwarded_subject_is_accepted(self):
        provider = RepsolProvider("luz")
        message = MailMessage("1", "<1>", "toni@albirconfort.es", f"RV: {provider.subject}", None, "")
        self.assertTrue(provider.matches(message))

    def test_veolia_extracts_only_invoice_link_from_allowlist(self):
        provider = VeoliaProvider()
        body = '<a href="https://u59752715.ct.sendgrid.net/ls/click?upn=x">Descargar factura</a><a href="https://evil.example/x">Factura</a>'
        message = MailMessage("1", "<1>", provider.sender, provider.subject, None, body)
        candidate = provider.extract_candidates(message)
        self.assertEqual(candidate.links, ["https://u59752715.ct.sendgrid.net/ls/click?upn=x"])

    def test_non_invoice_links_are_rejected(self):
        self.assertEqual(extract_allowed_links('<a href="https://agua.veolia.es/info">Política de privacidad</a>', VeoliaProvider.allowed_hosts), [])

    def test_repsol_keeps_only_invoice_portal_links(self):
        provider = RepsolProvider("luz")
        body = (
            '<a href="https://click.informativo.repsolluzygas.com/factura">Factura</a>'
            '<a href="https://click.informativo.repsolluzygas.com/consultar">Consultar factura</a>'
            '<a href="https://click.informativo.repsolluzygas.com/ayuda">Aprender a leer factura</a>'
        )
        message = MailMessage("1", "<1>", provider.sender, provider.subject, None, body)
        candidate = provider.extract_candidates(message)
        self.assertEqual(len(candidate.links), 2)
        self.assertTrue(all("ayuda" not in link for link in candidate.links))

