"""OCR + extraction tests.   Run:  python -m pytest tests -v"""
import os
from datetime import date

import pytest

from conftest import MY_GSTIN, SAMPLES
from core import organizer as org
from core.extractor import find_dates, gstin_checksum_ok, parse_invoice_text, repair_gstin
from core.ocr import run_ocr, tesseract_available

EXPECTED = {
    "01_ABC_Traders_INV-1023.pdf": ("ABC Traders", "06ABCPT1234K1ZP", "INV-1023", date(2026, 10, 5), 38291.0, 3),
    "02_XYZ_Enterprises_photo.jpg": ("XYZ Enterprises", "06AAJFX5678L1ZB", "XYZ/26-27/0457", date(2026, 10, 12), 15400.0, 2),
    "03_Shree_Ganesh_Steel_IGST.png": ("Shree Ganesh Steel Pvt Ltd", "27AACCS9876M1ZF", "SGS-8841", date(2026, 11, 3), 55814.0, 2),
    "04_ABC_Traders_scanned.pdf": ("ABC Traders", "06ABCPT1234K1ZP", "INV-0988", date(2026, 9, 18), 16992.0, 1),
    "05_ABC_Traders_INV-1023_copy.jpg": ("ABC Traders", "06ABCPT1234K1ZP", "INV-1023", date(2026, 10, 5), 38291.0, 3),
    "06_Krishna_Hardware_partial.pdf": ("Krishna Hardware Stores", "07AAKFK4321P1ZZ", "KH-2210", date(2026, 10, 20), 3540.0, 2),
}
MIME = {"pdf": "application/pdf", "jpg": "image/jpeg", "png": "image/png"}
needs_tesseract = pytest.mark.skipif(not tesseract_available(), reason="Tesseract not installed")


@needs_tesseract
@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_sample_invoices(name):
    data = open(os.path.join(SAMPLES, name), "rb").read()
    r = run_ocr(data, MIME[name.rsplit(".", 1)[1]])
    ex = parse_invoice_text(r.text, my_gstin=MY_GSTIN)
    supplier, gstin, inv, d, total, n_items = EXPECTED[name]
    assert ex.value("supplier_name") == supplier
    assert ex.value("supplier_gstin") == gstin
    assert ex.value("invoice_number") == inv
    assert ex.value("invoice_date") == d
    assert ex.value("total_amount") == total
    assert len(ex.items) == n_items
    assert ex.fields["total_amount"].confidence == "high"


def test_text_pdf_needs_no_tesseract():
    data = open(os.path.join(SAMPLES, "01_ABC_Traders_INV-1023.pdf"), "rb").read()
    r = run_ocr(data, "application/pdf")
    assert r.method == "pdf-text" and "INV-1023" in r.text


def test_never_invents_missing_values():
    text = """SOME SHOP
    Invoice No: A-77
    Item  Qty  Rate
    Taxable Value 1,000.00
    CGST @ 9% 90.00
    SGST @ 9% 90.00"""
    ex = parse_invoice_text(text)
    assert ex.value("total_amount") is None            # not printed -> not filled
    assert ex.fields["total_amount"].confidence == "missing"
    assert ex.value("invoice_date") is None
    assert ex.value("supplier_gstin") is None
    assert ex.value("payment_status") is None
    assert "total_amount" in ex.uncertain_fields()
    assert any("total not found" in n.lower() for n in ex.notes)


def test_mismatched_totals_are_flagged():
    text = """ABC Traders
    Invoice No: 5 Date: 01/04/2026
    Taxable Value 1,000.00
    CGST 90.00
    SGST 90.00
    Grand Total 1,500.00"""
    ex = parse_invoice_text(text)
    assert ex.value("total_amount") == 1500.0
    assert ex.fields["total_amount"].confidence != "high"
    assert any("does not equal" in n for n in ex.notes)


def test_own_gstin_is_never_the_supplier():
    text = f"Bill To: Me\nGSTIN: {MY_GSTIN}\nInvoice No: 1"
    ex = parse_invoice_text(text, my_gstin=MY_GSTIN)
    assert ex.value("supplier_gstin") is None


def test_gstin_checksum_and_repair():
    assert gstin_checksum_ok("27AACCS9876M1ZF")
    assert not gstin_checksum_ok("27AACCS9876M1ZG")
    assert repair_gstin("O6AAJFXS678L12B") == ("06AAJFX5678L1ZB", True)       # O->0, S->5, 2->Z
    assert repair_gstin("OG6AAJFXS678L12B") == ("06AAJFX5678L1ZB", True)      # extra character


def test_dates_indian_format():
    assert find_dates("Date: 05/10/2026")[0][0] == date(2026, 10, 5)
    assert find_dates("12-Oct-2026")[0][0] == date(2026, 10, 12)
    assert find_dates("Dated 3rd November, 2026")[0][0] == date(2026, 11, 3)
    assert find_dates("2026-01-31")[0][0] == date(2026, 1, 31)


def test_folders_and_names():
    assert org.auto_folder(date(2026, 10, 5), "ABC Traders") == "2026/October/ABC Traders"
    assert org.auto_folder(date(2026, 10, 5), None) == "2026/October/Other Suppliers"
    assert org.auto_folder(None, "M/s. ABC Traders") == "Undated/ABC Traders"
    assert org.unique_file_name("bill.pdf", ["bill.pdf", "bill (2).pdf"]) == "bill (3).pdf"
    assert org.safe_file_name("my/bill:01?.PDF") == "my bill 01.pdf"
    assert org.financial_year(date(2027, 2, 5)) == "2026-27"
    assert org.financial_year(date(2026, 4, 1)) == "2026-27"
    assert org.normalise_folder(" PurchaseDrive/2026//Misc ") == "2026/Misc"


def test_optional_ai_parser_is_checked(monkeypatch):
    """The AI parser's answer is validated like everything else (fake API, no network)."""
    from core import ai_parser

    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"content": [{"type": "text", "text": '{"supplier_name": "ABC Traders", '
                    '"supplier_gstin": "06ABCPT1234K1ZP", "invoice_number": "INV-1023", '
                    '"invoice_date": "2026-10-05", "taxable_amount": 32450, "cgst_amount": 2920.5, '
                    '"sgst_amount": 2920.5, "igst_amount": null, "total_amount": 38291, '
                    '"payment_status": null, "items": []}'}]}

    monkeypatch.setattr(ai_parser.requests, "post", lambda *a, **k: Resp())
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    text = "ABC Traders GSTIN 06ABCPT1234K1ZP Invoice No: INV-1023"
    ex = ai_parser.parse_with_claude(text)
    assert ex.parser == "claude"
    assert ex.fields["supplier_gstin"].confidence == "high"
    assert ex.fields["total_amount"].confidence == "high"      # sums check out
    assert ex.value("igst_amount") is None and ex.value("payment_status") is None
