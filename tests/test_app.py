"""Clicks through the real Streamlit pages (with the in-memory test backend)."""
import os

import pytest
from streamlit.testing.v1 import AppTest

from conftest import MY_GSTIN, ROOT, SAMPLES
from core import services as sv
from core.backend import _MEMORY, MemoryBackend, reset_memory_store

EMAIL, PW = "owner@example.com", "password123"


def seed():
    """Two saved bills + one bill waiting for review."""
    reset_memory_store()
    b = MemoryBackend()
    b.sign_up(EMAIL, PW)
    b.sign_in(EMAIL, PW)
    uid = b.current_user().id
    b.save_settings(uid, {"my_gstin": MY_GSTIN, "ocr_enabled": True})
    for name, pay in (("01_ABC_Traders_INV-1023.pdf", "unpaid"),
                      ("06_Krishna_Hardware_partial.pdf", "partially_paid")):
        data = open(os.path.join(SAMPLES, name), "rb").read()
        out = sv.upload_to_inbox(b, uid, name, data)
        ex, _ = sv.extract_invoice(b, out.row, data, {"my_gstin": MY_GSTIN})
        form = {k: ex.value(k) for k in ex.fields}
        form["payment_status"] = pay
        form["amount_paid"] = 1000 if pay == "partially_paid" else None
        sv.save_invoice(b, uid, out.row, form, ex.items)
    data = open(os.path.join(SAMPLES, "03_Shree_Ganesh_Steel_IGST.png"), "rb").read()
    out = sv.upload_to_inbox(b, uid, "03_Shree_Ganesh_Steel_IGST.png", data)
    # simulate an OCR result stored for review (works without Tesseract too)
    b.update_invoice(out.row["id"], {"extraction": {"fields": {
        "supplier_name": {"value": "Shree Ganesh Steel Pvt Ltd", "confidence": "high"},
        "invoice_number": {"value": "SGS-8841", "confidence": "high"},
        "invoice_date": {"value": "2026-11-03", "confidence": "high"},
        "total_amount": {"value": 55814.0, "confidence": "medium"},
        "igst_amount": {"value": 8514.0, "confidence": "medium"},
        "taxable_amount": {"value": 47300.0, "confidence": "medium"}}, "items": [], "notes": []}})
    return uid


def logged_in_app():
    at = AppTest.from_file(os.path.join(ROOT, "app.py"), default_timeout=90)
    at.run()
    at.text_input[0].input(EMAIL)
    at.text_input[1].input(PW)
    at.button[0].click()
    at.run()
    assert not at.exception, at.exception
    return at


def go(at, page):
    at.switch_page(f"views/{page}.py")
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def test_login_required():
    seed()
    at = AppTest.from_file(os.path.join(ROOT, "app.py"), default_timeout=60)
    at.run()
    assert not at.exception
    assert any("PurchaseDrive" in m.value for m in at.markdown)
    assert not at.metric  # no data visible before login


def test_wrong_password():
    seed()
    at = AppTest.from_file(os.path.join(ROOT, "app.py"), default_timeout=60)
    at.run()
    at.text_input[0].input(EMAIL)
    at.text_input[1].input("nope")
    at.button[0].click()
    at.run()
    assert any("Wrong email or password" in e.value for e in at.error)


def test_dashboard_numbers():
    seed()
    at = logged_in_app()
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["Invoices saved"] == "2"
    assert metrics["Total purchases"] == "₹41,831.00"
    assert metrics["Total GST recorded"] == "₹6,381.00"
    assert metrics["Balance due (unpaid)"] == "₹40,831.00"
    assert any("waiting for you" in w.value for w in at.warning)


@pytest.mark.parametrize("page", ["dashboard", "upload", "all_bills", "suppliers", "folders",
                                  "search", "details", "settings"])
def test_every_page_renders(page):
    seed()
    at = logged_in_app()
    go(at, page)
    assert at.title, "page has a title"


def test_review_and_save_files_the_bill():
    seed()
    at = go(logged_in_app(), "upload")
    assert any("1 waiting" in s.value for s in at.subheader)
    save = [b for b in at.button if "Save & file" in str(b.label)]
    assert save, "save button present"
    save[0].click()
    at.run()
    assert not at.exception
    rows = [r for r in _MEMORY["invoices"].values()
            if r["original_file_name"] == "03_Shree_Ganesh_Steel_IGST.png"]
    assert rows[0]["review_status"] == "confirmed"
    assert rows[0]["folder_path"] == "2026/November/Shree Ganesh Steel Pvt Ltd"
    assert rows[0]["file_name"] == "2026-11-03_SGS-8841_Shree Ganesh Steel Pvt Ltd.png"
    assert rows[0]["igst_amount"] == 8514.0


def test_upload_through_the_browser():
    seed()
    at = go(logged_in_app(), "upload")
    data = open(os.path.join(SAMPLES, "04_ABC_Traders_scanned.pdf"), "rb").read()
    at.file_uploader[0].upload("scan.pdf", data, "application/pdf")
    at.run()
    btn = [b for b in at.button if "Upload 1 file" in str(b.label)]
    btn[0].click()
    at.run()
    assert not at.exception
    assert any(r["original_file_name"] == "scan.pdf" for r in _MEMORY["invoices"].values())


def test_details_edit_payment():
    seed()
    at = go(logged_in_app(), "details")
    sel = at.selectbox(key="open_invoice")
    target = next(r["id"] for r in _MEMORY["invoices"].values() if r.get("invoice_number") == "KH-2210")
    sel.set_value(target)
    at.run()
    status = [s for s in at.selectbox if str(s.label).startswith("Payment status")][0]
    status.set_value("paid")
    [b for b in at.button if "Save changes" in str(b.label)][0].click()
    at.run()
    assert not at.exception
    assert _MEMORY["invoices"][target]["payment_status"] == "paid"


def test_search_by_supplier():
    seed()
    at = go(logged_in_app(), "search")
    [t for t in at.text_input if t.label == "Supplier name"][0].input("krishna")
    [b for b in at.button if "Search" in str(b.label)][0].click()
    at.run()
    assert {m.label: m.value for m in at.metric}["Bills found"] == "1"


def test_backup_button():
    seed()
    at = go(logged_in_app(), "settings")
    [b for b in at.button if "Prepare backup" in str(b.label)][0].click()
    at.run()
    assert not at.exception
    assert any("Backup ready: 3 bills" in s.value for s in at.success)
