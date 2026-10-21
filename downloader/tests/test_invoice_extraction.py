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

HIDRAQUA_LAYOUT_TEXT = """DATOS DEL CONTRATO
N\ufffdm. contrato: 165068
Titular: Empresa de prueba S.L.U.
NIF: B53101911
Direcci\ufffdn: CALLE DEL AGUA 15 B/B-2-C
03581 ALFAZ DEL PI
DATOS DE FACTURACI\ufffdN
N\ufffdm factura 01352026A100043736
Periodo 2026/04
Fecha emisi\ufffdn 14-10-2026
TOTAL A PAGAR 74,49 \u20acCONSUMO TOTAL 24 m3
Contador \ufffd mm Lectura anterior Lectura actual Consumo m3 Base fact.
J19OA016921Z 15 13-07-26 502 13-10-26 526 24 Real
AGUA (1) 36,26
CUOTA DE SERVICIO 21,13 10
CONSUMO Tramo 1 24 0,6306 15,13 10
ALCANTARILLADO (2) 12,51
CONSUMO Bloque Unico 24 0,5212 12,51 10
I.V.A al 10 % BASE IMPONIBLE: 48,77 4,88
CANON SANEAMIENTO (3) 20,84
CUOTA DE SERVICIO 10,95 NS
CONSUMO 24 0,412 9,89 NS
"""

REPSOL_GAS_LAYOUT_TEXT = """Factura de gas
Titular: Empresa de prueba SL
CIF/NIF: B12345678
Dirección de
Suministro:
CALLE DE PRUEBA 12 1 A
03500 MUNICIPIO (Alicante)
Tipo: Mercado libre N° de Contrato: 4300000000
CUPS: ES0222122000039942VG
TOTAL FACTURA 1.000,91 €
Periodo de Facturación: Del 26.06.2026 al 18.08.2026
Término Fijo 17,94 €
Término Energía 9.032 kWh 792,64 €
Alquiler de contador 1,01 €
Impuesto Esp. Hidrocarburos 9.032 kWh 0,001729 €/kWh 15,61 €
Base imponible 827,20 €
I.V.A. (21%) de 827,20 173,71 €
Tarifa de Acceso: RL.2
N° de contador 3414022
Distribuidora: NEDGIA CEGAS
Fecha y N° Factura 31.08.2026 / 10GLN01000353682
Fecha de cobro 01.09.2026
Importe ATR: 129,21 €
Importe Cargos 0,08 €
Lectura actual Real 18.08.2026 4.243 m3
Lectura anterior Real 25.06.2026 3.539 m3
Consumo en m3 704 m3
Factor de conversión 12,829545 kWh/m3
Consumo en kWh 9.032 kWh
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
    def test_hidraqua_water_layout_extracts_meter_readings_and_charge_totals(self):
        fields = parse_invoice_text(HIDRAQUA_LAYOUT_TEXT, "agua", "hidraqua")

        self.assertEqual(fields["holder"], "Empresa de prueba S.L.U.")
        self.assertEqual(fields["tax_id"], "B53101911")
        self.assertEqual(fields["supply_address"], "CALLE DEL AGUA 15 B/B-2-C 03581 ALFAZ DEL PI")
        self.assertEqual(fields["contract_number"], "165068")
        self.assertEqual(fields["invoice_number"], "01352026A100043736")
        self.assertEqual(fields["period_start"], "13-07-26")
        self.assertEqual(fields["period_end"], "13-10-26")
        self.assertEqual(fields["meter_number"], "J19OA016921Z")
        self.assertEqual(fields["meter_size_mm"], "15")
        self.assertEqual(fields["previous_reading_date"], "13-07-26")
        self.assertEqual(fields["previous_reading"], "502")
        self.assertEqual(fields["current_reading_date"], "13-10-26")
        self.assertEqual(fields["current_reading"], "526")
        self.assertEqual(fields["consumption_m3"], "24")
        self.assertEqual(fields["water_amount"], "36,26")
        self.assertEqual(fields["sewerage_amount"], "12,51")
        self.assertEqual(fields["sanitation_amount"], "20,84")
        self.assertEqual(fields["vat_rate"], "10")
        self.assertEqual(fields["vat_base"], "48,77")
        self.assertEqual(fields["vat_amount"], "4,88")
        self.assertEqual(fields["total_amount"], "74,49")
        self.assertEqual(fields["extraction_status"], "Correcta")

    def test_hidraqua_workbook_includes_water_bill_breakdown_columns(self):
        with TemporaryDirectory() as temporary_root:
            destination = Path(temporary_root) / "facturas-agua.xlsx"
            document = SimpleNamespace(file_path="not-opened.pdf", provider="hidraqua")
            fields = parse_invoice_text(HIDRAQUA_LAYOUT_TEXT, "agua", "hidraqua")
            with patch("downloader.invoice_extraction.extract_invoice_fields", return_value=fields):
                build_invoice_workbook([document], "agua", destination)

            workbook = load_workbook(destination, read_only=True, data_only=True)
            sheet = workbook["Agua"]
            values = dict(zip((cell.value for cell in sheet[1]), (cell.value for cell in sheet[2])))
            self.assertEqual(values["Titular"], "Empresa de prueba S.L.U.")
            self.assertEqual(values["Lectura anterior"], 502)
            self.assertEqual(values["Lectura actual"], 526)
            self.assertEqual(values["Agua (€)"], 36.26)
            self.assertEqual(values["Alcantarillado (€)"], 12.51)
            self.assertEqual(values["Canon de saneamiento (€)"], 20.84)
            self.assertEqual(values["IVA (€)"], 4.88)
            self.assertEqual(values["Estado de extracción"], "Correcta")
            workbook.close()

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

    def test_repsol_gas_layout_extracts_readings_and_invoice_breakdown(self):
        fields = parse_invoice_text(REPSOL_GAS_LAYOUT_TEXT, "gas", "repsol")

        self.assertEqual(fields["invoice_number"], "10GLN01000353682")
        self.assertEqual(fields["issue_date"], "31.08.2026")
        self.assertEqual(fields["contract_number"], "4300000000")
        self.assertEqual(fields["period_start"], "26.06.2026")
        self.assertEqual(fields["period_end"], "18.08.2026")
        self.assertEqual(fields["due_date"], "01.09.2026")
        self.assertEqual(fields["cups"], "ES0222122000039942VG")
        self.assertEqual(fields["meter_number"], "3414022")
        self.assertEqual(fields["distributor"], "NEDGIA CEGAS")
        self.assertEqual(fields["tariff"], "Mercado libre")
        self.assertEqual(fields["access_tariff"], "RL.2")
        self.assertEqual(fields["previous_reading_date"], "25.06.2026")
        self.assertEqual(fields["previous_reading"], "3.539")
        self.assertEqual(fields["current_reading_date"], "18.08.2026")
        self.assertEqual(fields["current_reading"], "4.243")
        self.assertEqual(fields["consumption_m3"], "704")
        self.assertEqual(fields["conversion_factor_kwh_m3"], "12,829545")
        self.assertEqual(fields["consumption_kwh"], "9.032")
        self.assertEqual(fields["fixed_term_amount"], "17,94")
        self.assertEqual(fields["energy_amount"], "792,64")
        self.assertEqual(fields["meter_rental_amount"], "1,01")
        self.assertEqual(fields["hydrocarbon_tax_amount"], "15,61")
        self.assertEqual(fields["atr_amount"], "129,21")
        self.assertEqual(fields["charges_amount"], "0,08")
        self.assertEqual(fields["vat_rate"], "21")
        self.assertEqual(fields["vat_base"], "827,20")
        self.assertEqual(fields["vat_amount"], "173,71")
        self.assertEqual(fields["total_amount"], "1.000,91")
        self.assertEqual(fields["extraction_status"], "Correcta")

    def test_gas_workbook_converts_spanish_grouped_integers_to_numbers(self):
        with TemporaryDirectory() as temporary_root:
            destination = Path(temporary_root) / "facturas-gas.xlsx"
            document = SimpleNamespace(file_path="not-opened.pdf", provider="repsol")
            fields = parse_invoice_text(REPSOL_GAS_LAYOUT_TEXT, "gas", "repsol")
            with patch("downloader.invoice_extraction.extract_invoice_fields", return_value=fields):
                build_invoice_workbook([document], "gas", destination)

            workbook = load_workbook(destination, read_only=True, data_only=True)
            sheet = workbook["Gas"]
            values = dict(zip((cell.value for cell in sheet[1]), (cell.value for cell in sheet[2])))
            self.assertEqual(values["Consumo (kWh)"], 9032)
            self.assertEqual(values["Lectura anterior (m³)"], 3539)
            self.assertEqual(values["Lectura actual (m³)"], 4243)
            self.assertEqual(values["Base IVA (€)"], 827.2)
            self.assertEqual(values["IVA (€)"], 173.71)
            self.assertEqual(values["Estado de extracción"], "Correcta")
            workbook.close()

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
