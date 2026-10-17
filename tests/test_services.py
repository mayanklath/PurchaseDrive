"""Upload / save / duplicate / delete / backup tests on the in-memory test backend."""
import os

import pytest

from conftest import MY_GSTIN, SAMPLES
from core import analytics as an
from core import services as sv
from core.backend import BackendError, MemoryBackend, reset_memory_store


def sample(name):
    return open(os.path.join(SAMPLES, name), "rb").read()


@pytest.fixture
def be():
    reset_memory_store()
    b = MemoryBackend()
    b.sign_up("me@example.com", "password1")
    b.sign_in("me@example.com", "password1")
    return b


def upload_and_save(be, name, **overrides):
    data = sample(name)
    out = sv.upload_to_inbox(be, be.current_user().id, name, data)
    assert out.status == "uploaded"
    ex, err = sv.extract_invoice(be, out.row, data, {"my_gstin": MY_GSTIN})
    if ex is None:   # no Tesseract on this machine: type the values like a user would
        form = overrides
    else:
        form = {k: ex.value(k) for k in ex.fields}
        form.update(overrides)
    form["payment_status"] = form.get("payment_status") or "unknown"
    return sv.save_invoice(be, be.current_user().id, out.row, form, ex.items if ex else [])


def test_upload_goes_to_cloud_inbox_first(be):
    out = sv.upload_to_inbox(be, be.current_user().id, "bill.pdf", sample("01_ABC_Traders_INV-1023.pdf"))
    assert out.row["storage_path"].endswith("/PurchaseDrive/Inbox/bill.pdf")
    assert out.row["review_status"] == "pending"
    assert be.download_file(out.row["storage_path"]) == sample("01_ABC_Traders_INV-1023.pdf")


def test_save_files_into_year_month_supplier(be):
    row = upload_and_save(be, "01_ABC_Traders_INV-1023.pdf")
    assert row["folder_path"] == "2026/October/ABC Traders"
    assert row["file_name"] == "2026-10-05_INV-1023_ABC Traders.pdf"
    assert row["financial_year"] == "2026-27"
    assert len(be.get_items(row["id"])) == 3
    assert be.download_file(row["storage_path"])  # file really moved


def test_never_overwrites(be):
    a = sv.upload_to_inbox(be, be.current_user().id, "x.pdf", sample("01_ABC_Traders_INV-1023.pdf"))
    b = sv.upload_to_inbox(be, be.current_user().id, "x.pdf", sample("06_Krishna_Hardware_partial.pdf"))
    assert a.row["file_name"] == "x.pdf" and b.row["file_name"] == "x (2).pdf"


def test_exact_same_file_is_caught(be):
    upload_and_save(be, "01_ABC_Traders_INV-1023.pdf")
    out = sv.upload_to_inbox(be, be.current_user().id, "again.pdf", sample("01_ABC_Traders_INV-1023.pdf"))
    assert out.status == "exact_duplicate"


@pytest.mark.skipif(not __import__("core.ocr", fromlist=["x"]).tesseract_available(),
                    reason="Tesseract not installed")
def test_duplicate_invoice_not_double_counted(be):
    upload_and_save(be, "01_ABC_Traders_INV-1023.pdf")
    dup = upload_and_save(be, "05_ABC_Traders_INV-1023_copy.jpg")
    assert dup["is_duplicate"] is True
    m = an.dashboard_metrics(an.to_df(be.list_invoices()))
    assert m["invoices"] == 1 and m["purchase_total"] == 38291.0 and m["duplicates"] == 1


def test_manual_changes_and_bad_input(be):
    row = upload_and_save(be, "06_Krishna_Hardware_partial.pdf")
    row2 = sv.save_invoice(be, be.current_user().id, row,
                           {"supplier_name": "Krishna HW", "invoice_number": "KH-2210",
                            "invoice_date": "2026-10-20", "total_amount": 3540,
                            "payment_status": "partially_paid", "amount_paid": 1000},
                           [], file_name="my bill", folder="Misc/Hardware")
    assert row2["folder_path"] == "Misc/Hardware" and row2["file_name"] == "my bill.pdf"
    df = an.to_df(be.list_invoices())
    assert an.dashboard_metrics(df)["balance_due"] == 2540
    with pytest.raises(ValueError):
        sv.save_invoice(be, be.current_user().id, row2, {"supplier_gstin": "123"}, [])


def test_rejects_fake_files(be):
    out = sv.upload_to_inbox(be, be.current_user().id, "virus.pdf", b"MZ\x90\x00not a pdf")
    assert out.status == "error"
    out = sv.upload_to_inbox(be, be.current_user().id, "notes.txt", b"hello")
    assert out.status == "error"


def test_users_cannot_see_each_other(be):
    upload_and_save(be, "01_ABC_Traders_INV-1023.pdf")
    other = MemoryBackend()
    other.sign_up("other@example.com", "password2")
    other.sign_in("other@example.com", "password2")
    assert other.list_invoices() == []
    with pytest.raises(BackendError):
        other.download_file(be.list_invoices()[0]["storage_path"])


def test_delete_removes_file_and_record(be):
    row = upload_and_save(be, "01_ABC_Traders_INV-1023.pdf")
    sv.delete_invoice(be, row)
    assert be.list_invoices() == []
    with pytest.raises(BackendError):
        be.download_file(row["storage_path"])


def test_backup_and_restore(be):
    upload_and_save(be, "01_ABC_Traders_INV-1023.pdf")
    upload_and_save(be, "06_Krishna_Hardware_partial.pdf")
    z, stats = sv.build_backup_zip(be)
    assert stats["invoices"] == 2 and not stats["missing_files"]
    other = MemoryBackend()
    other.sign_up("new@example.com", "password3")
    other.sign_in("new@example.com", "password3")
    res = sv.restore_backup_zip(other, other.current_user().id, z)
    assert res["restored"] == 2 and not res["errors"]
    assert sv.restore_backup_zip(other, other.current_user().id, z)["skipped_existing"] == 2
    paths = sorted(r["folder_path"] for r in other.list_invoices())
    assert paths == sorted(r["folder_path"] for r in be.list_invoices())


def test_search_filters(be):
    upload_and_save(be, "01_ABC_Traders_INV-1023.pdf", payment_status="unpaid")
    upload_and_save(be, "06_Krishna_Hardware_partial.pdf", payment_status="paid")
    df = an.to_df(be.list_invoices())
    assert len(an.filter_invoices(df, supplier="krishna")) == 1
    assert len(an.filter_invoices(df, invoice_number="1023")) == 1
    assert len(an.filter_invoices(df, gstin="07AAKFK")) == 1
    assert len(an.filter_invoices(df, text="38291")) == 1
    assert len(an.filter_invoices(df, payment=["paid"])) == 1
    assert len(an.filter_invoices(df, financial_year="2026-27")) == 2
    assert len(an.filter_invoices(df, amount_min=10000)) == 1
    from datetime import date
    assert len(an.filter_invoices(df, date_from=date(2026, 10, 10), date_to=date(2026, 10, 31))) == 1
    assert len(an.filter_invoices(df, file_name="krishna")) == 1
