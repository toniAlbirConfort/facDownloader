from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlparse
import re
import unicodedata

from bs4 import BeautifulSoup
from django.conf import settings


def normalize(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(char for char in value if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", value).strip().casefold()


def normalize_subject(value: str) -> str:
    subject = normalize(value)
    return re.sub(r"^(?:(?:re|rv|fwd|fw)\s*:\s*)+", "", subject)


@dataclass(frozen=True)
class MailMessage:
    id: str
    internet_message_id: str
    sender: str
    subject: str
    received_at: str | None
    body: str
    has_attachments: bool = False
    attachments: tuple["MailAttachment", ...] = ()


@dataclass(frozen=True)
class MailAttachment:
    name: str
    content_type: str
    content: bytes


@dataclass(frozen=True)
class InvoiceCandidateData:
    provider: str
    invoice_type: str
    links: list[str]
    metadata: dict


class InvoiceProvider(Protocol):
    key: str
    invoice_type: str
    sender: str
    subject: str
    allowed_hosts: frozenset[str]

    def matches(self, message: MailMessage) -> bool: ...
    def extract_candidates(self, message: MailMessage) -> InvoiceCandidateData: ...


def extract_allowed_links(body: str, allowed_hosts: frozenset[str]) -> list[str]:
    soup = BeautifulSoup(body or "", "html.parser")
    links = []
    for anchor in soup.find_all("a", href=True):
        url = anchor["href"].strip()
        parsed = urlparse(url)
        label = normalize(anchor.get_text(" ", strip=True))
        if parsed.scheme not in {"http", "https"} or parsed.hostname not in allowed_hosts:
            continue
        if not any(term in label for term in ("factura", "descargar", "consultar")):
            continue
        if url not in links:
            links.append(url)
    return links


class BaseProvider:
    def matches(self, message: MailMessage) -> bool:
        configured_test_senders = getattr(settings, "INVOICE_TEST_SENDERS", set())
        sender_matches = normalize(message.sender) == normalize(self.sender) or normalize(message.sender) in {
            normalize(sender) for sender in configured_test_senders
        }
        return sender_matches and normalize_subject(message.subject) == normalize_subject(self.subject)

    def extract_candidates(self, message: MailMessage) -> InvoiceCandidateData:
        return InvoiceCandidateData(
            provider=self.key,
            invoice_type=self.invoice_type,
            links=extract_allowed_links(message.body, self.allowed_hosts),
            metadata={},
        )


class RepsolProvider(BaseProvider):
    key = "repsol"
    allowed_hosts = frozenset({"click.informativo.repsolluzygas.com", "informativo.repsolluzygas.com", "www.repsol.es", "repsol.es"})

    def __init__(self, invoice_type: str):
        if invoice_type not in {"gas", "luz"}:
            raise ValueError("Tipo Repsol no soportado")
        self.invoice_type = invoice_type
        self.sender = "tufactura@informativo.repsolluzygas.com"
        self.subject = f"Tienes una nueva factura de {invoice_type}."

    def extract_candidates(self, message: MailMessage) -> InvoiceCandidateData:
        soup = BeautifulSoup(message.body or "", "html.parser")
        links = []
        for anchor in soup.find_all("a", href=True):
            url = anchor["href"].strip()
            parsed = urlparse(url)
            label = normalize(anchor.get_text(" ", strip=True))
            if parsed.scheme not in {"http", "https"} or parsed.hostname not in self.allowed_hosts:
                continue
            if label not in {"factura", "consultar factura"}:
                continue
            if url not in links:
                links.append(url)
        return InvoiceCandidateData(
            provider=self.key,
            invoice_type=self.invoice_type,
            links=links,
            metadata={"portal": "repsol", "requires_identity": True},
        )


class VeoliaProvider(BaseProvider):
    key = "veolia"
    invoice_type = "agua"
    sender = "noreply@es.veolia.com"
    subject = "Ya tienes disponible tu factura de agua"
    allowed_hosts = frozenset({
        "u59752715.ct.sendgrid.net",
        "agua.veolia.es",
        "www.agua.veolia.es",
        "aguasonline.es",
        "www.aguasonline.es",
        "hidraqua.veolia.es",
        "www.hidraqua.veolia.es",
    })


def provider_for(invoice_type: str) -> InvoiceProvider:
    if invoice_type in {"gas", "luz"}:
        return RepsolProvider(invoice_type)
    if invoice_type == "agua":
        return VeoliaProvider()
    raise ValueError(f"Tipo de factura no soportado: {invoice_type}")

