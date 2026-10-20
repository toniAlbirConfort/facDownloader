import codecs
import base64
import email
import imaplib
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from email.header import decode_header, make_header
from email.message import Message
from email.utils import parseaddr, parsedate_to_datetime
from pathlib import Path
from tempfile import NamedTemporaryFile

from django.conf import settings
from django.utils import timezone

from .crypto import decrypt
from .graph import DownloadedFile, GraphError
from .models import Mailbox, MailboxCredential
from .providers import MailAttachment, MailMessage


CHARSET_ALIASES = {
    "we8iso8859p1": "iso-8859-1",
}


def decode_payload(payload: bytes, charset: str | None) -> str:
    normalized = (charset or "").strip().casefold().replace("_", "-")
    encoding = CHARSET_ALIASES.get(normalized, normalized or "utf-8")
    try:
        codecs.lookup(encoding)
    except LookupError:
        encoding = "cp1252"
    return payload.decode(encoding, errors="replace")


def decode_modified_utf7(value: str) -> str:
    """Decode the modified UTF-7 used by IMAP folder names."""
    decoded = []
    position = 0
    while position < len(value):
        if value[position] != "&":
            decoded.append(value[position])
            position += 1
            continue
        end = value.find("-", position + 1)
        if end == -1:
            decoded.append(value[position:])
            break
        encoded = value[position + 1:end]
        if not encoded:
            decoded.append("&")
        else:
            try:
                encoded = encoded.replace(",", "/")
                encoded += "=" * ((4 - len(encoded) % 4) % 4)
                decoded.append(base64.b64decode(encoded).decode("utf-16-be"))
            except (ValueError, UnicodeError):
                decoded.append(value[position:end + 1])
        position = end + 1
    return "".join(decoded)


def parse_list_folder(value: bytes | str) -> dict | None:
    line = value.decode("ascii", errors="replace") if isinstance(value, bytes) else value
    match = re.match(r"^\((?P<flags>[^)]*)\)\s+(?:NIL|\"(?:\\\\.|[^\"])*\")\s+(?P<name>.*)$", line)
    if not match:
        return None
    name = match.group("name").strip()
    if name.upper() == "NIL":
        return None
    if name.startswith('"') and name.endswith('"'):
        name = re.sub(r"\\(.)", r"\1", name[1:-1])
    return {
        "name": decode_modified_utf7(name),
        "has_children": "\\HasChildren" in match.group("flags"),
    }


def decode_mime_header(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except (LookupError, UnicodeError):
        return value


def parse_received_at(value: str | None) -> str | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed.tzinfo is None:
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed.isoformat()


def part_text(part: Message) -> str:
    try:
        value = part.get_content()
        return value if isinstance(value, str) else str(value)
    except (LookupError, UnicodeError):
        payload = part.get_payload(decode=True) or b""
        return decode_payload(payload, part.get_content_charset())


def parse_message(uid: str, raw: bytes) -> MailMessage:
    message = email.message_from_bytes(raw, policy=email.policy.default)
    sender = parseaddr(decode_mime_header(message.get("From")))[1] or decode_mime_header(message.get("From"))
    subject = decode_mime_header(message.get("Subject"))
    internet_message_id = message.get("Message-ID", "")
    html_body = ""
    plain_body = ""
    attachments: list[MailAttachment] = []

    parts = message.walk() if message.is_multipart() else (message,)
    for part in parts:
        content_disposition = part.get_content_disposition()
        filename = decode_mime_header(part.get_filename())
        content_type = part.get_content_type()
        if content_disposition == "attachment" or filename:
            attachments.append(MailAttachment(filename or "attachment", content_type, part.get_payload(decode=True) or b""))
            continue
        if content_type == "text/html" and not html_body:
            html_body = part_text(part)
        elif content_type == "text/plain" and not plain_body:
            plain_body = part_text(part)

    return MailMessage(
        id=uid,
        internet_message_id=internet_message_id,
        sender=sender,
        subject=subject,
        received_at=parse_received_at(message.get("Date")),
        body=html_body or plain_body,
        has_attachments=bool(attachments),
        attachments=tuple(attachments),
    )


def imap_date(value: date) -> str:
    return value.strftime("%d-%b-%Y")


class ImapClient:
    def __init__(self, mailbox: Mailbox, folder: str | None = None, auto_select: bool = True):
        try:
            credential = mailbox.imap_credential
        except MailboxCredential.DoesNotExist as exc:
            raise GraphError("El buzón no tiene configuración IMAP") from exc

        try:
            password = decrypt(credential.password_ciphertext)
            if credential.use_ssl:
                self.connection = imaplib.IMAP4_SSL(credential.host, credential.port, timeout=settings.GRAPH_TIMEOUT_SECONDS)
            else:
                self.connection = imaplib.IMAP4(credential.host, credential.port, timeout=settings.GRAPH_TIMEOUT_SECONDS)
            self.connection.login(credential.username, password)
            if auto_select:
                self.select_folder(folder or credential.folder or "INBOX")
        except (imaplib.IMAP4.error, OSError, ValueError) as exc:
            raise GraphError(f"No se pudo conectar al servidor IMAP: {exc}") from exc

    def select_folder(self, folder: str):
        status, _ = self.connection.select(folder, readonly=True)
        if status != "OK":
            raise GraphError(f"No se pudo abrir la carpeta IMAP {folder}")

    def list_folders(self) -> list[dict]:
        status, data = self.connection.list()
        if status != "OK":
            raise GraphError("El servidor IMAP no pudo listar las carpetas")
        folders = [parsed for item in data or [] if item and (parsed := parse_list_folder(item))]
        if not any(folder["name"].casefold() == "inbox" for folder in folders):
            folders.append({"name": "INBOX", "has_children": False})
        return sorted(folders, key=lambda folder: (folder["name"].casefold() != "inbox", folder["name"].casefold()))

    def close(self):
        try:
            self.connection.close()
        except imaplib.IMAP4.error:
            pass
        try:
            self.connection.logout()
        except imaplib.IMAP4.error:
            pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    def list_messages(self, start: date, end: date) -> list[MailMessage]:
        status, data = self.connection.uid(
            "search",
            None,
            "SINCE",
            imap_date(start),
            "BEFORE",
            imap_date(end + timedelta(days=1)),
        )
        if status != "OK":
            raise GraphError("El servidor IMAP no pudo buscar mensajes")

        messages = []
        for uid in (data[0] or b"").split():
            status, fetched = self.connection.uid("fetch", uid, "(RFC822)")
            if status != "OK":
                continue
            raw = next((item[1] for item in fetched if isinstance(item, tuple)), None)
            if raw:
                messages.append(parse_message(uid.decode(), raw))
        return messages

    @staticmethod
    def attachment_file(attachment: MailAttachment) -> DownloadedFile:
        with NamedTemporaryFile(delete=False, dir=settings.DOWNLOAD_TEMP_ROOT, suffix=".download") as target:
            target.write(attachment.content)
            return DownloadedFile(Path(target.name), attachment.content_type, len(attachment.content))

