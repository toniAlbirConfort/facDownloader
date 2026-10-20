from email.message import EmailMessage

from django.test import SimpleTestCase

from downloader.imap import part_text


class ImapTextTests(SimpleTestCase):
    def test_decodes_oracle_iso_charset_alias(self):
        message = EmailMessage()
        message.set_payload("Factura de gas: año".encode("iso-8859-1"))
        message.set_type("text/plain")
        message.set_param("charset", "we8iso8859p1")

        self.assertEqual(part_text(message), "Factura de gas: año")

    def test_unknown_charset_uses_western_european_fallback(self):
        message = EmailMessage()
        message.set_payload("Factura: año €".encode("cp1252"))
        message.set_type("text/plain")
        message.set_param("charset", "unknown-mail-charset")

        self.assertEqual(part_text(message), "Factura: año €")
