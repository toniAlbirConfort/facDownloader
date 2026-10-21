from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

import fitz
from openpyxl import load_workbook
from PIL import Image
from django.test import SimpleTestCase

from downloader.invoice_extraction import build_invoice_workbook, extract_invoice_fields, parse_invoice_text


INVOICE_TEXT = """Factura de electricidad
Titular: Albir Confort SL
NIF: B12345678
Dirección de suministro: Calle Mayor 10, Benidorm
Número de factura: ELEC-2026-0001
Fecha de emisión: 12/10/2026
Periodo desde: 01/10/2026
Periodo hasta: 31/10/2026
CUPS ES0021000000000000AA
Potencia contratada 4,60 kW
Consumo total 250,50 kWh
Tarifa 2.0TD
Base imponible: 100,00
Impuestos: 21,00
Total a pagar: 121,00 EUR
Vencimiento: 30/10/2026
"""

REPSOL_LAYOUT_TEXT = """Factura de luz
Titular: Empresa de prueba SL
CUPS ES0021000000476692GZ1F
N\ufffd de contrato 4304360208
N\ufffd contrato de acceso 227233750
N\ufffd de factura 61092609638
Fecha de emisi\ufffdn 14/10/2026
Direcci\ufffdn de suministro AV EJEMPLO 34
ALFA DEL PI (ALICANTE)
Total factura 174,57 \u20ac
T\ufffdrmino fijo 43,84 \u20ac
Energ\ufffda 91,91 \u20ac
Otros conceptos 1,54 \u20ac
Impuestos 37,28 \u20ac
Periodo de facturaci\ufffdn 12/09/2026 - 12/10/2026
D\ufffdas facturados 30 D\ufffdas
Consumo en este periodo 747,61 kWh
Fecha de cobro 15/10/2026
Periodo de consumo Punta Llano Valle
Consumo del periodo (actual) hasta 12/10/2026
185,52 kWh 182,28 kWh 379,82 kWh
DATOS T\ufffdCNICOS DE POTENCIA
Potencia contratada 8,8kW 8,8kW
Tipo de tarifa Mercado libre
Peaje de transporte y distribuci\ufffdn
2.0TD
N\ufffd de contador 0042055850
Impuesto El\ufffdctrico 6,98 \u20ac 136,49 x 5,11269632%
IVA (21 %) de 144,27 30,30 \u20ac
"""


def write_pdf(path: Path, text: str | None = None, image_only: bool = False):
    pdf = fitz.open()
    page = pdf.new_page()
    if image_only:
        image = Image.new("RGB", (900, 1200), "white")
        from PIL import ImageDraw
        ImageDraw.Draw(image).text((35, 35), text or "", fill="black", spacing=8)
        output = BytesIO()
        image.save(output, format="PNG")
        page.insert_image(page.rect, stream=output.getvalue())
    elif text:
        page.insert_textbox(fitz.Rect(40, 40, 550, 790), text, fontsize=10)
    pdf.save(path)
    pdf.close()


class InvoiceExtractionTests(SimpleTestCase):
    def test_repsol_layout_extracts_fields_with_broken_pdf_glyphs(self):
        fields = parse_invoice_text(REPSOL_LAYOUT_TEXT, "luz", "repsol")

        self.assertEqual(fields["invoice_number"], "61092609638")
        self.assertEqual(fields["contract_number"], "4304360208")
        self.assertEqual(fields["access_contract_number"], "227233750")
        self.assertEqual(fields["issue_date"], "14/10/2026")
        self.assertEqual(fields["supply_address"], "AV EJEMPLO 34 ALFA DEL PI (ALICANTE)")
        self.assertEqual(fields["period_start"], "12/09/2026")
        self.assertEqual(fields["period_end"], "12/10/2026")
        self.assertEqual(fields["due_date"], "15/10/2026")
        self.assertEqual(fields["cups"], "ES0021000000476692GZ1F")
        self.assertEqual(fields["meter_number"], "0042055850")
        self.assertEqual(fields["billing_days"], "30")
        self.assertEqual(fields["consumption_peak_kwh"], "185,52")
        self.assertEqual(fields["consumption_flat_kwh"], "182,28")
        self.assertEqual(fields["consumption_valley_kwh"], "379,82")
        self.assertEqual(fields["contracted_power_kw"], "8,8")
        self.assertEqual(fields["contracted_power_valley_kw"], "8,8")
        self.assertEqual(fields["access_tariff"], "2.0TD")
        self.assertEqual(fields["fixed_term_amount"], "43,84")
        self.assertEqual(fields["energy_amount"], "91,91")
        self.assertEqual(fields["other_concepts_amount"], "1,54")
        self.assertEqual(fields["electricity_tax_amount"], "6,98")
        self.assertEqual(fields["electricity_tax_base"], "136,49")
        self.assertEqual(fields["vat_amount"], "30,30")
        self.assertEqual(fields["vat_base"], "144,27")
        self.assertEqual(fields["extraction_status"], "Correcta")

    def test_text_pdf_extracts_common_and_electricity_fields_without_ocr(self):
        with TemporaryDirectory() as temporary_root:
            path = Path(temporary_root) / "factura.pdf"
            write_pdf(path, INVOICE_TEXT)
            with patch("downloader.invoice_extraction.pytesseract.image_to_string") as ocr:
                fields = extract_invoice_fields(path, "luz", "repsol")

        self.assertEqual(fields["invoice_number"], "ELEC-2026-0001")
        self.assertEqual(fields["issue_date"], "12/10/2026")
        self.assertEqual(fields["total_amount"], "121,00")
        self.assertEqual(fields["holder"], "Albir Confort SL")
        self.assertEqual(fields["consumption_kwh"], "250,50")
        self.assertEqual(fields["extraction_status"], "Correcta")
        ocr.assert_not_called()

    def test_scanned_pdf_uses_local_ocr_fallback(self):
        with TemporaryDirectory() as temporary_root:
            path = Path(temporary_root) / "escaneada.pdf"
            write_pdf(path, image_only=True)
            with patch("downloader.invoice_extraction.pytesseract.image_to_string", return_value=INVOICE_TEXT) as ocr:
                fields = extract_invoice_fields(path, "luz", "repsol")

        self.assertGreater(ocr.call_count, 0)
        self.assertEqual(fields["invoice_number"], "ELEC-2026-0001")
        self.assertEqual(fields["extraction_status"], "Correcta")

    def test_missing_or_unreadable_fields_are_marked_for_review(self):
        with TemporaryDirectory() as temporary_root:
            path = Path(temporary_root) / "invalid.pdf"
            path.write_bytes(b"not a PDF")
            fields = extract_invoice_fields(path, "agua", "veolia")

        self.assertEqual(fields["extraction_status"], "Revisar")
        self.assertIn("número de factura", fields["missing_fields"])
        self.assertIn("ocr_note", fields)

    def test_parser_includes_service_specific_columns_and_blank_unknowns(self):
        fields = parse_invoice_text("Número de factura: GAS-1\nTotal a pagar: 55,20", "gas", "repsol")

        self.assertIn("cups", fields)
        self.assertIn("meter_number", fields)
        self.assertIn("consumption_kwh", fields)
        self.assertEqual(fields["cups"], "")
        self.assertEqual(fields["extraction_status"], "Revisar")

    def test_workbook_has_one_row_per_pdf_and_review_columns(self):
        with TemporaryDirectory() as temporary_root:
            root = Path(temporary_root)
            path = root / "factura.pdf"
            write_pdf(path, INVOICE_TEXT)
            destination = root / "facturas-luz.xlsx"
            documents = [SimpleNamespace(file_path=str(path), provider="repsol")]
            build_invoice_workbook(documents, "luz", destination)
            workbook = load_workbook(destination, read_only=True)
            sheet = workbook["Luz"]

            self.assertEqual(sheet.max_row, 2)
            headers = [cell.value for cell in sheet[1]]
            values = dict(zip(headers, [cell.value for cell in sheet[2]]))
            self.assertEqual(values["Número de factura"], "ELEC-2026-0001")
            self.assertEqual(values["Total (€)"], 121.0)
            self.assertEqual(values["Estado de extracción"], "Correcta")
            self.assertIn("Potencia contratada (kW)", headers)
            workbook.close()

    def test_repsol_workbook_includes_contract_consumption_and_tax_breakdown(self):
        with TemporaryDirectory() as temporary_root:
            destination = Path(temporary_root) / "facturas-luz.xlsx"
            document = SimpleNamespace(file_path="not-opened.pdf", provider="repsol")
            fields = parse_invoice_text(REPSOL_LAYOUT_TEXT, "luz", "repsol")
            with patch("downloader.invoice_extraction.extract_invoice_fields", return_value=fields):
                build_invoice_workbook([document], "luz", destination)

            workbook = load_workbook(destination, read_only=True, data_only=True)
            sheet = workbook["Luz"]
            values = dict(zip((cell.value for cell in sheet[1]), (cell.value for cell in sheet[2])))
            self.assertEqual(values["Número de contrato"], "4304360208")
            self.assertEqual(values["Contrato de acceso"], "227233750")
            self.assertEqual(values["CUPS"], "ES0021000000476692GZ1F")
            self.assertEqual(values["Consumo punta (kWh)"], 185.52)
            self.assertEqual(values["IVA (€)"], 30.3)
            self.assertEqual(values["Base IVA (€)"], 144.27)
            self.assertEqual(values["Estado de extracción"], "Correcta")
            workbook.close()
