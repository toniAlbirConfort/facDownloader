import re
from pathlib import Path
from tempfile import NamedTemporaryFile

from django.conf import settings

from .crypto import decrypt
from .graph import DownloadedFile, GraphError
from .models import ProviderCredential


def _repsol_identity_values() -> list[str]:
    credential = ProviderCredential.objects.filter(provider="repsol").first()
    if not credential or not credential.secret_ciphertext:
        raise GraphError("Falta configurar el CIF/NIF de Repsol en Administración")
    values = [value.strip() for value in decrypt(credential.secret_ciphertext).splitlines() if value.strip()]
    if not values:
        raise GraphError("La credencial de Repsol no contiene ningún CIF/NIF")
    return values


def _first_visible(locator):
    for index in range(locator.count()):
        candidate = locator.nth(index)
        if candidate.is_visible():
            return candidate
    return None


def _identity_input(page):
    by_label = _first_visible(page.get_by_label(re.compile(r"NIF/CIF|pasaporte", re.IGNORECASE)))
    if by_label:
        return by_label
    selectors = (
        'input[name*="nif" i]',
        'input[id*="nif" i]',
        'input[placeholder*="NIF" i]',
        'input[type="text"]',
    )
    for selector in selectors:
        field = _first_visible(page.locator(selector))
        if field:
            return field
    raise GraphError("No se encontró el campo NIF/CIF del portal Repsol")


def _consult_button(page):
    button = _first_visible(page.get_by_role("button", name=re.compile(r"consultar factura", re.IGNORECASE)))
    if button:
        return button
    button = _first_visible(page.get_by_text(re.compile(r"consultar factura", re.IGNORECASE)))
    if button:
        return button
    raise GraphError("No se encontró el botón Consultar factura del portal Repsol")


def _download_button(page):
    button = _first_visible(page.get_by_role("button", name=re.compile(r"^descargar$", re.IGNORECASE)))
    if button:
        return button
    button = _first_visible(page.get_by_text(re.compile(r"^descargar$", re.IGNORECASE)))
    if button:
        return button
    raise GraphError("No se encontró el botón Descargar de la factura Repsol")


def _dismiss_cookie_consent(page):
    # OneTrust may inject the overlay shortly after DOMContentLoaded. Give it
    # a moment to appear so it cannot intercept the form submission below.
    page.wait_for_timeout(1000)
    for label in ("Solo necesarias", "Aceptar todas"):
        button = _first_visible(
            page.get_by_role("button", name=re.compile(f"^{re.escape(label)}$", re.IGNORECASE))
        )
        if not button:
            button = _first_visible(page.get_by_text(label, exact=True))
        if button:
            button.click()
            page.wait_for_timeout(250)
            return


def download_repsol_invoice(source_links: list[str]) -> DownloadedFile:
    if not source_links:
        raise GraphError("El correo de Repsol no contiene un enlace de factura válido")

    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise GraphError("Playwright no está disponible en el portal-worker") from exc

    timeout_ms = int(settings.GRAPH_TIMEOUT_SECONDS * 1000)
    last_error = None
    identities = _repsol_identity_values()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            for source_link in source_links:
                for identity in identities:
                    context = browser.new_context(accept_downloads=True)
                    page = context.new_page()
                    page.set_default_timeout(timeout_ms)
                    try:
                        page.goto(source_link, wait_until="domcontentloaded", timeout=timeout_ms)
                        _dismiss_cookie_consent(page)
                        field = _identity_input(page)
                        field.fill(identity)
                        _consult_button(page).click()
                        # The invoice viewer is rendered asynchronously after
                        # the form submission. Wait for the actual control as
                        # well as a short settling period so a cold portal
                        # response cannot look like a missing button.
                        page.wait_for_timeout(3000)
                        download_locator = page.get_by_role(
                            "button", name=re.compile(r"^descargar$", re.IGNORECASE)
                        ).first
                        try:
                            download_locator.wait_for(
                                state="visible", timeout=min(timeout_ms, 15000)
                            )
                        except PlaywrightTimeoutError:
                            pass
                        download_button = _download_button(page)
                        with page.expect_download(timeout=timeout_ms) as download_info:
                            download_button.click()
                        download = download_info.value
                        with NamedTemporaryFile(delete=False, dir=settings.DOWNLOAD_TEMP_ROOT, suffix=".download") as target:
                            target_path = Path(target.name)
                        download.save_as(str(target_path))
                        size = target_path.stat().st_size
                        if size > 50 * 1024 * 1024:
                            target_path.unlink(missing_ok=True)
                            raise GraphError("La factura Repsol supera el límite de 50 MB")
                        return DownloadedFile(target_path, "application/pdf", size)
                    except (GraphError, PlaywrightTimeoutError, OSError) as exc:
                        last_error = exc
                    finally:
                        context.close()
        finally:
            browser.close()

    if isinstance(last_error, GraphError):
        raise last_error
    raise GraphError("No se pudo descargar la factura Repsol con los CIF/NIF configurados") from last_error

