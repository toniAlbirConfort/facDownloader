Attempting to perform the InitializeDefaultDrives operation on the 'FileSystem' provider failed.
import base64
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Iterator
from urllib.parse import urlparse

import httpx
import msal
from django.conf import settings


class GraphError(RuntimeError):
    pass


@dataclass(frozen=True)
class DownloadedFile:
    path: Path
    content_type: str
    size_bytes: int


class GraphClient:
    base_url = "https://graph.microsoft.com/v1.0"

    def __init__(self):
        if not all((settings.GRAPH_TENANT_ID, settings.GRAPH_CLIENT_ID, settings.GRAPH_CLIENT_SECRET)):
            raise GraphError("Faltan las credenciales de Microsoft Graph")
        self.app = msal.ConfidentialClientApplication(
            settings.GRAPH_CLIENT_ID,
            authority=f"https://login.microsoftonline.com/{settings.GRAPH_TENANT_ID}",
            client_credential=settings.GRAPH_CLIENT_SECRET,
        )
        token = self.app.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])
        if "access_token" not in token:
            raise GraphError("No se pudo obtener el token de Microsoft Graph")
        self.headers = {"Authorization": f"Bearer {token['access_token']}"}
        self.timeout = settings.GRAPH_TIMEOUT_SECONDS

    def request(self, method: str, url: str, **kwargs):
        headers = {**self.headers, **kwargs.pop("headers", {})}
        response = httpx.request(method, url, headers=headers, timeout=self.timeout, follow_redirects=True, **kwargs)
        if response.status_code == 429:
            raise GraphError("Microsoft Graph ha limitado la petición; reintentar más tarde")
        if response.is_error:
            raise GraphError(f"Microsoft Graph devolvió HTTP {response.status_code}")
        return response

    def list_messages(self, mailbox: str, start_iso: str, end_iso: str, sender: str, subject: str) -> list[dict]:
        url = f"{self.base_url}/users/{mailbox}/mailFolders/inbox/messages"
        filter_value = f"receivedDateTime ge {start_iso} and receivedDateTime lt {end_iso} and from/emailAddress/address eq '{sender}' and subject eq '{subject}'"
        params = {
            "$select": "id,internetMessageId,receivedDateTime,subject,from,body,hasAttachments",
            "$filter": filter_value,
            "$top": "100",
        }
        result = []
        while url:
            response = self.request("GET", url, params=params, headers={"Prefer": "outlook.body-content-type=html"})
            payload = response.json()
            result.extend(payload.get("value", []))
            url = payload.get("@odata.nextLink")
            params = None
        return result

    def list_attachments(self, mailbox: str, message_id: str) -> list[dict]:
        url = f"{self.base_url}/users/{mailbox}/messages/{message_id}/attachments"
        response = self.request("GET", url, params={"$top": "100"})
        return response.json().get("value", [])

    def download_attachment(self, mailbox: str, message_id: str, attachment: dict) -> DownloadedFile:
        content = attachment.get("contentBytes")
        content_type = attachment.get("contentType", "application/octet-stream")
        if content:
            data = base64.b64decode(content)
            return self._write_temp(data, content_type)
        url = f"{self.base_url}/users/{mailbox}/messages/{message_id}/attachments/{attachment['id']}/$value"
        response = self.request("GET", url)
        return self._write_temp(response.content, content_type)

    @staticmethod
    def _write_temp(data: bytes, content_type: str) -> DownloadedFile:
        with NamedTemporaryFile(delete=False, dir=settings.DOWNLOAD_TEMP_ROOT, suffix=".download") as target:
            target.write(data)
            return DownloadedFile(Path(target.name), content_type, len(data))

    def download_url(self, url: str, allowed_hosts: frozenset[str] | None = None) -> DownloadedFile:
        return download_url(url, allowed_hosts=allowed_hosts)


def download_url(url: str, allowed_hosts: frozenset[str] | None = None) -> DownloadedFile:
    with httpx.stream("GET", url, timeout=settings.GRAPH_TIMEOUT_SECONDS, follow_redirects=True) as response:
        if response.status_code >= 400:
            raise GraphError(f"El proveedor devolvió HTTP {response.status_code}")
        if allowed_hosts and urlparse(str(response.url)).hostname not in allowed_hosts:
            raise GraphError("El enlace redirige a un dominio no autorizado")
        content_type = response.headers.get("content-type", "application/octet-stream").split(";", 1)[0]
        with NamedTemporaryFile(delete=False, dir=settings.DOWNLOAD_TEMP_ROOT, suffix=".download") as target:
            size = 0
            for chunk in response.iter_bytes(1024 * 1024):
                size += len(chunk)
                if size > 50 * 1024 * 1024:
                    raise GraphError("La descarga supera el límite de 50 MB")
                target.write(chunk)
            return DownloadedFile(Path(target.name), content_type, size)

