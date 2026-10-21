"""Local PDF text/OCR extraction and spreadsheet generation for invoices."""

import re
from decimal import Decimal, InvalidOperation
from pathlib import Path

import fitz
import pytesseract
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from PIL import Image


COMMON_FIELDS = (
    ("Tipo", "invoice_type"),
    ("Proveedor", "provider"),
    ("Titular", "holder"),
    ("NIF/CIF", "tax_id"),
    ("Dirección de suministro", "supply_address"),
    ("Número de factura", "invoice_number"),
    ("Fecha de emisión", "issue_date"),
    ("Periodo desde", "period_start"),
    ("Periodo hasta", "period_end"),
    ("Base imponible (€)", "tax_base"),
    ("Impuestos (€)", "tax_amount"),
    ("Total (€)", "total_amount"),
    ("Vencimiento", "due_date"),
)

SERVICE_FIELDS = {
    "agua": (
        ("Contador", "meter_number"),
        ("Lectura anterior", "previous_reading"),
        ("Lectura actual", "current_reading"),
        ("Consumo (m³)", "consumption_m3"),
    ),
    "luz": (
        ("CUPS", "cups"),
        ("Potencia contratada (kW)", "contracted_power_kw"),
        ("Consumo (kWh)", "consumption_kwh"),
        ("Tarifa", "tariff"),
    ),
    "gas": (
        ("CUPS", "cups"),
        ("Contador", "meter_number"),
        ("Consumo (kWh)", "consumption_kwh"),
        ("Tarifa", "tariff"),
    ),
}

REQUIRED_FIELDS = ("invoice_number", "issue_date", "total_amount")
NUMERIC_FIELDS = {
    "tax_base", "tax_amount", "total_amount", "previous_reading", "current_reading",
    "consumption_m3", "consumption_kwh", "contracted_power_kw",
}
MISSING_LABELS = {
    "invoice_number": "número de factura",
    "issue_date": "fecha de emisión",
    "total_amount": "total",
}


def _label_value(text: str, labels: str) -> str:
    match = re.search(
        rf"(?:{labels})\s*(?:n[.º°]?\s*)?[:#\-]?\s*([^\r\n|]{{2,100}})",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        return ""
    value = re.sub(r"\s{2,}", " ", match.group(1)).strip(" \t:;-")
    return "" if value.casefold() in {"", "n/a", "no disponible"} else value


def _amount_after_label(text: str, labels: str) -> str:
    match = re.search(
        rf"(?:{labels})[^\d\r\n]{{0,40}}(\d[\d. ]*(?:,\d{{2}}|\.\d{{2}}))\s*(?:€|eur)?",
        text,
        flags=re.IGNORECASE,
    )
    return match.group(1).replace(" ", "") if match else ""


def _amount_with_unit(text: str, labels: str, unit: str) -> str:
    match = re.search(
        rf"(?:{labels})[^\d\r\n]{{0,40}}(\d[\d. ]*(?:,\d{{1,3}}|\.\d{{1,3}})?)\s*{unit}\b",
        text,
        flags=re.IGNORECASE,
    )
    return match.group(1).replace(" ", "") if match else ""


def parse_invoice_text(text: str, invoice_type: str, provider: str) -> dict[str, str]:
    """Extract common Spanish utility invoice values using local text rules."""
    fields = {key: "" for _, key in (*COMMON_FIELDS, *SERVICE_FIELDS[invoice_type])}
    fields.update({"invoice_type": invoice_type, "provider": provider, "extraction_status": "Revisar", "missing_fields": ""})

    fields["holder"] = _label_value(text, r"titular(?:\s+del\s+contrato)?|cliente")
    fields["tax_id"] = _label_value(text, r"(?:NIF|CIF|DNI|NIE)(?:\s*/\s*(?:NIF|CIF))?")
    fields["supply_address"] = _label_value(text, r"direcci[oó]n\s+(?:del\s+)?(?:suministro|punto\s+de\s+suministro)")
    fields["invoice_number"] = _label_value(text, r"n[uú]mero\s+de\s+factura|factura\s+n[uú]m(?:ero)?\.?|n[uú]m(?:ero)?\.?\s+factura")

    date_pattern = r"(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})"
    for key, labels in (
        ("issue_date", r"fecha\s+(?:de\s+)?(?:emisi[oó]n|factura)"),
        ("period_start", r"(?:periodo|per[ií]odo)\s+(?:de\s+)?(?:consumo\s+)?desde"),
        ("period_end", r"(?:periodo|per[ií]odo)\s+(?:de\s+)?(?:consumo\s+)?hasta"),
        ("due_date", r"(?:fecha\s+de\s+)?vencimiento|pagar\s+antes\s+de"),
    ):
        match = re.search(rf"(?:{labels})[^\d\r\n]{{0,24}}{date_pattern}", text, flags=re.IGNORECASE)
        if match:
            fields[key] = match.group(1)

    fields["tax_base"] = _amount_after_label(text, r"base\s+imponible")
    fields["tax_amount"] = _amount_after_label(text, r"(?:total\s+)?impuestos|impuestos\s+totales|IVA")
    fields["total_amount"] = _amount_after_label(
        text,
        r"total\s+a\s+pagar|importe\s+total|total\s+factura|(?<![\w])total(?=\s*[:\-])",
    )

    cups = re.search(r"\b([A-Z]{2}\d{16,20}[A-Z]{2})\b", text, flags=re.IGNORECASE)
    if cups and invoice_type in {"luz", "gas"}:
        fields["cups"] = cups.group(1).upper()

    if invoice_type == "agua":
        fields["meter_number"] = _label_value(text, r"(?:n[uú]mero\s+de\s+)?contador|n[uú]m\.\s*contador")
        fields["previous_reading"] = _amount_after_label(text, r"lectura\s+anterior")
        fields["current_reading"] = _amount_after_label(text, r"lectura\s+actual|lectura\s+nueva")
        fields["consumption_m3"] = _amount_with_unit(text, r"consumo(?:\s+de\s+agua)?", r"m[³3]")
    else:
        fields["consumption_kwh"] = _amount_with_unit(text, r"(?:consumo|energ[ií]a)(?:\s+total)?", r"kWh")
        fields["tariff"] = _label_value(text, r"tarifa|peaje\s+de\s+acceso")
        if invoice_type == "luz":
            fields["contracted_power_kw"] = _amount_with_unit(text, r"potencia\s+contratada", r"kW")
        else:
            fields["meter_number"] = _label_value(text, r"(?:n[uú]mero\s+de\s+)?contador|n[uú]m\.\s*contador")

    missing = [key for key in REQUIRED_FIELDS if not fields.get(key)]
    fields["missing_fields"] = ", ".join(MISSING_LABELS[key] for key in missing)
    fields["extraction_status"] = "Correcta" if not missing else "Revisar"
    return fields


def extract_invoice_fields(pdf_path: str | Path, invoice_type: str, provider: str) -> dict[str, str]:
    """Use embedded PDF text first, then local Spanish/English OCR if needed."""
    try:
        with fitz.open(pdf_path) as pdf:
            page_texts = [page.get_text("text") for page in pdf]
            text = "\n".join(page_texts)
            fields = parse_invoice_text(text, invoice_type, provider)
            if fields["extraction_status"] == "Correcta":
                return fields

            try:
                ocr_text = []
                with fitz.open(pdf_path) as pdf:
                    for page in pdf:
                        pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
                        image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
                        ocr_text.append(pytesseract.image_to_string(image, lang="spa+eng", config="--psm 6"))
                ocr_fields = parse_invoice_text("\n".join([text, *ocr_text]), invoice_type, provider)
                if len(ocr_fields["missing_fields"].split(", ")) < len(fields["missing_fields"].split(", ")):
                    fields = ocr_fields
            except Exception as exc:  # OCR is a fallback; retain any embedded text already found.
                fields["ocr_note"] = f"OCR no disponible: {exc}"
            return fields
    except Exception as exc:
        fields = parse_invoice_text("", invoice_type, provider)
        fields["ocr_note"] = f"No se pudo leer el PDF: {exc}"
        return fields


def build_invoice_workbook(documents: list, invoice_type: str, destination: str | Path) -> Path:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = invoice_type.capitalize()
    fields = [*COMMON_FIELDS, *SERVICE_FIELDS[invoice_type], ("Estado de extracción", "extraction_status"), ("Campos pendientes", "missing_fields")]
    sheet.append([label for label, _ in fields])
    header_fill = PatternFill("solid", fgColor="17365D")
    for cell in sheet[1]:
        cell.font = Font(color="FFFFFF", bold=True)
        cell.fill = header_fill
    for document in documents:
        extracted = extract_invoice_fields(document.file_path, invoice_type, document.provider)
        row = []
        for _, key in fields:
            value = extracted.get(key, "")
            if value and key in NUMERIC_FIELDS:
                normalized = value.replace(" ", "")
                if "," in normalized:
                    normalized = normalized.replace(".", "").replace(",", ".")
                try:
                    value = float(Decimal(normalized))
                except InvalidOperation:
                    pass
            row.append(value)
        sheet.append(row)
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    for column in sheet.columns:
        letter = column[0].column_letter
        width = min(max(max(len(str(cell.value or "")) for cell in column) + 2, 12), 42)
        sheet.column_dimensions[letter].width = width
    headers = {cell.value: cell.column for cell in sheet[1]}
    for label, key in fields:
        if key in NUMERIC_FIELDS:
            for row in range(2, sheet.max_row + 1):
                sheet.cell(row, headers[label]).number_format = "#,##0.00"
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(destination)
    return destination
