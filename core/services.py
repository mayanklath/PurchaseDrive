"""Business logic: upload, OCR, review & save, duplicates, delete, backup & restore.

Every function receives the logged-in user's backend, so Supabase Row Level
Security is always enforced on top of these checks.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timezone

from core import organizer as org
from core.backend import BackendError, ITEM_FIELDS
from core.config import ALLOWED_TYPES, MAX_FILE_MB, MIME_BY_EXT, ROOT_FOLDER
from core.extractor import AMOUNT_FIELDS, Extraction, parse_amount, parse_invoice_text

PAYMENT_STATUSES = ["unpaid", "partially_paid", "paid", "unknown"]
PAYMENT_LABELS = {"unpaid": "Unpaid", "partially_paid": "Partially paid", "paid": "Paid",
                  "unknown": "Not set"}

_MAGIC = {"application/pdf": [b"%PDF"], "image/png": [b"\x89PNG"],
          "image/jpeg": [b"\xff\xd8\xff"]}


# =====================================================================
#  Upload
# =====================================================================
def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def mime_for(file_name: str) -> str | None:
    ext = file_name.rpartition(".")[2].lower()
    return MIME_BY_EXT.get(ext)


def validate_upload(file_name: str, data: bytes) -> str | None:
    """Return an error message, or None when the file is OK."""
    ext = file_name.rpartition(".")[2].lower()
    if ext not in ALLOWED_TYPES:
        return f"'{file_name}': only JPG, JPEG, PNG and PDF files are allowed."
    if len(data) == 0:
        return f"'{file_name}' is empty."
    if len(data) > MAX_FILE_MB * 1024 * 1024:
        return f"'{file_name}' is larger than {MAX_FILE_MB} MB."
    mime = MIME_BY_EXT[ext]
    if not any(data.startswith(m) for m in _MAGIC[mime]):
        return f"'{file_name}' does not look like a real {ext.upper()} file."
    return None


@dataclass
class UploadOutcome:
    status: str                 # "uploaded" | "exact_duplicate" | "error"
    message: str
    row: dict | None = None
    existing: dict | None = None


def upload_to_inbox(backend, user_id: str, file_name: str, data: bytes,
                    allow_duplicate: bool = False) -> UploadOutcome:
    """Step 1: put the ORIGINAL file safely into the cloud (Inbox folder) at once."""
    err = validate_upload(file_name, data)
    if err:
        return UploadOutcome("error", err)
    digest = sha256(data)
    if not allow_duplicate:
        same = backend.find_by_sha(digest)
        if same:
            return UploadOutcome("exact_duplicate",
                                 f"'{file_name}' was already uploaded as "
                                 f"'{same[0]['file_name']}'.", existing=same[0])
    clean = org.safe_file_name(file_name)
    folder = org.inbox_folder(user_id)
    name = org.unique_file_name(clean, backend.list_folder(folder))
    path = f"{folder}/{name}"
    mime = mime_for(file_name)
    try:
        backend.upload_file(path, data, mime)
    except BackendError as exc:
        if "exists" in str(exc).lower():  # someone uploaded the same name at the same moment
            name = org.unique_file_name(clean, backend.list_folder(folder) + [name])
            path = f"{folder}/{name}"
            backend.upload_file(path, data, mime)
        else:
            return UploadOutcome("error", f"'{file_name}': {exc}")
    try:
        row = backend.insert_invoice({
            "user_id": user_id, "original_file_name": file_name, "file_name": name,
            "storage_path": path, "folder_path": "Inbox", "mime_type": mime,
            "file_size": len(data), "file_sha256": digest, "review_status": "pending",
        })
    except BackendError as exc:
        backend.delete_files([path])  # do not leave an orphan file behind
        return UploadOutcome("error", f"'{file_name}': {exc}")
    return UploadOutcome("uploaded", f"'{file_name}' saved to the cloud.", row=row)


# =====================================================================
#  OCR / extraction
# =====================================================================
def extract_invoice(backend, row: dict, data: bytes, settings: dict) -> tuple[Extraction | None, str | None]:
    """Run OCR + field extraction and store the suggestions on the invoice row.

    Returns (extraction, error_message). Nothing is written into the real invoice
    columns here - that only happens when you press "Save" after reviewing.
    """
    from core import ocr
    from core.ai_parser import ai_enabled, parse_with_claude

    try:
        result = ocr.run_ocr(data, row["mime_type"])
    except ocr.OCRUnavailable as exc:
        return None, str(exc)
    except Exception as exc:  # corrupt file etc.
        return None, f"Could not read this file: {exc}"

    my_gstin = settings.get("my_gstin")
    my_name = settings.get("business_name")
    ex = None
    if ai_enabled():
        try:
            ex = parse_with_claude(result.text, my_gstin, my_name)
        except Exception as exc:
            ex = None
            ai_error = f"AI parser unavailable ({exc.__class__.__name__}); used built-in parser."
    if ex is None:
        ex = parse_invoice_text(result.text, my_gstin, my_name)
        if ai_enabled():
            ex.notes.insert(0, ai_error)
    ex.notes.extend(result.warnings)
    if result.method == "tesseract" and result.confidence is not None and result.confidence < 60:
        ex.notes.append(f"The photo is hard to read (OCR confidence {result.confidence:.0f}%). "
                        "A sharper, well-lit photo will give better results.")
    payload = ex.as_dict()
    payload["ocr_method"] = result.method
    try:
        backend.update_invoice(row["id"], {
            "ocr_text": result.text[:200000],
            "ocr_confidence": round(result.confidence, 2) if result.confidence is not None else None,
            "extraction": payload,
        })
    except BackendError:
        pass  # suggestions are still shown on screen
    return ex, None


def known_supplier_name(rows: list[dict], gstin: str | None, name: str | None) -> str | None:
    """Reuse the spelling you already saved for this supplier (same GSTIN or same name)."""
    confirmed = [r for r in rows if r.get("review_status") == "confirmed" and r.get("supplier_name")]
    if gstin:
        for r in confirmed:
            if (r.get("supplier_gstin") or "").upper() == gstin.upper():
                return r["supplier_name"]
    if name:
        for r in confirmed:
            if r["supplier_name"].strip().lower() == name.strip().lower():
                return r["supplier_name"]
    return None


# =====================================================================
#  Save after review (also used when editing later)
# =====================================================================
def _norm_inv(n) -> str:
    return "".join(ch for ch in str(n or "").upper() if ch.isalnum())


def find_duplicate(rows: list[dict], invoice_id: str | None, supplier_name, gstin,
                   invoice_number) -> dict | None:
    """Same invoice number from the same supplier = the same bill."""
    if not invoice_number:
        return None
    key = _norm_inv(invoice_number)
    candidates = []
    for r in rows:
        if r["id"] == invoice_id or r.get("review_status") != "confirmed":
            continue
        if _norm_inv(r.get("invoice_number")) != key:
            continue
        same_gst = gstin and (r.get("supplier_gstin") or "").upper() == gstin.upper()
        same_name = supplier_name and (r.get("supplier_name") or "").strip().lower() == \
            supplier_name.strip().lower()
        if same_gst or same_name:
            candidates.append(r)
    candidates = [c for c in candidates if not c.get("is_duplicate")] or candidates
    return sorted(candidates, key=lambda r: r["created_at"])[0] if candidates else None


def clean_form(form: dict) -> dict:
    """Validate & normalise what the user typed. Raises ValueError with a friendly message."""
    out = {}
    out["supplier_name"] = (form.get("supplier_name") or "").strip() or None
    g = (form.get("supplier_gstin") or "").replace(" ", "").upper() or None
    if g and len(g) != 15:
        raise ValueError("GSTIN must be exactly 15 characters (or leave it empty).")
    out["supplier_gstin"] = g
    out["invoice_number"] = (form.get("invoice_number") or "").strip() or None
    d = form.get("invoice_date")
    if isinstance(d, str) and d:
        d = date.fromisoformat(d)
    if isinstance(d, datetime):
        d = d.date()
    out["invoice_date"] = d.isoformat() if d else None
    out["financial_year"] = org.financial_year(d) if d else None
    for k in AMOUNT_FIELDS + ["amount_paid"]:
        v = form.get(k)
        v = parse_amount(v) if v not in (None, "") else None
        if v is not None and v < 0:
            raise ValueError(f"{k.replace('_', ' ').title()} cannot be negative.")
        out[k] = v
    ps = form.get("payment_status") or "unknown"
    if ps not in PAYMENT_STATUSES:
        raise ValueError("Unknown payment status.")
    out["payment_status"] = ps
    if ps == "paid" and out["amount_paid"] is None and out["total_amount"] is not None:
        out["amount_paid"] = out["total_amount"]
    if ps == "unpaid":
        out["amount_paid"] = out["amount_paid"] or 0
    out["notes"] = (form.get("notes") or "").strip() or None
    return out


def clean_items(items: list[dict]) -> list[dict]:
    out = []
    for it in items or []:
        row = {k: it.get(k) for k in ITEM_FIELDS}
        for k in ("quantity", "rate", "taxable_amount", "gst_rate"):
            v = row.get(k)
            try:
                row[k] = None if v in (None, "") or v != v else float(v)
            except (TypeError, ValueError):
                row[k] = None
        for k in ("description", "hsn_code", "unit"):
            v = row.get(k)
            row[k] = None if v is None or v != v or str(v).strip() == "" else str(v).strip()
        if not any(row[k] is not None for k in ("description", "hsn_code", "taxable_amount")):
            continue
        row["line_no"] = len(out) + 1
        out.append(row)
    return out


def save_invoice(backend, user_id: str, row: dict, form: dict, items: list[dict],
                 file_name: str | None = None, folder: str | None = None,
                 all_rows: list[dict] | None = None) -> dict:
    """Confirm an invoice: store the details, move the file into its folder, flag duplicates."""
    values = clean_form(form)
    d = date.fromisoformat(values["invoice_date"]) if values["invoice_date"] else None
    target_folder = org.normalise_folder(folder) if folder and folder.strip() else \
        org.auto_folder(d, values["supplier_name"])
    if target_folder.lower() == "inbox":
        target_folder = org.auto_folder(d, values["supplier_name"])
    ext = row["file_name"].rpartition(".")[2].lower()
    if not (file_name or "").strip() and row.get("review_status") == "pending":
        file_name = org.suggested_file_name(row["original_file_name"], d,
                                            values["invoice_number"], values["supplier_name"])
    wanted = org.safe_file_name(file_name or row["file_name"])
    if not wanted.lower().endswith("." + ext):
        wanted = org.safe_file_name(wanted.rsplit(".", 1)[0] + "." + ext)

    old_path = row["storage_path"]
    new_path = org.storage_path(user_id, target_folder, wanted)
    moved = False
    if new_path != old_path:
        existing = [n for n in backend.list_folder(org.storage_path(user_id, target_folder, "")
                                                   .rstrip("/"))
                    if f"{target_folder}/{n}" != f"{row['folder_path']}/{row['file_name']}"]
        wanted = org.unique_file_name(wanted, existing)
        new_path = org.storage_path(user_id, target_folder, wanted)
        if new_path != old_path:
            backend.move_file(old_path, new_path)
            moved = True

    rows = all_rows if all_rows is not None else backend.list_invoices()
    dup = find_duplicate(rows, row["id"], values["supplier_name"], values["supplier_gstin"],
                         values["invoice_number"])
    changes = {**values, "folder_path": target_folder, "file_name": wanted,
               "storage_path": new_path, "review_status": "confirmed",
               "is_duplicate": bool(dup), "duplicate_of": dup["id"] if dup else None}
    try:
        updated = backend.update_invoice(row["id"], changes)
        backend.replace_items(row["id"], clean_items(items))
    except BackendError:
        if moved:  # put the file back so the database and storage stay in sync
            try:
                backend.move_file(new_path, old_path)
            except BackendError:
                pass
        raise
    return updated


def set_not_duplicate(backend, invoice_id: str) -> None:
    backend.update_invoice(invoice_id, {"is_duplicate": False, "duplicate_of": None})


def set_payment(backend, invoice_id: str, status: str, amount_paid=None) -> dict:
    if status not in PAYMENT_STATUSES:
        raise ValueError("Unknown payment status")
    return backend.update_invoice(invoice_id, {"payment_status": status,
                                               "amount_paid": parse_amount(amount_paid)})


def delete_invoice(backend, row: dict) -> None:
    """Delete the cloud file AND its database record (the caller must confirm first)."""
    try:
        backend.delete_files([row["storage_path"]])
    except BackendError:
        pass  # file already gone - still remove the record
    backend.delete_invoice(row["id"])


# =====================================================================
#  Backup & restore
# =====================================================================
BACKUP_VERSION = 1


def _rel_path(row: dict) -> str:
    return f"{row['folder_path']}/{row['file_name']}"


def build_backup_zip(backend, progress=None) -> tuple[bytes, dict]:
    """ZIP with every original file + all invoice data (JSON and Excel-friendly CSV)."""
    rows = backend.list_invoices(full=True)
    items = backend.list_all_items()
    stats = {"invoices": len(rows), "items": len(items), "missing_files": []}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        manifest = {"app": "PurchaseDrive AI", "version": BACKUP_VERSION,
                    "exported_at": datetime.now(timezone.utc).isoformat(),
                    "invoices": rows, "items": items}
        z.writestr("data/backup.json", json.dumps(manifest, indent=1, default=str))
        for name, data in (("data/invoices.csv", rows), ("data/invoice_items.csv", items)):
            s = io.StringIO()
            if data:
                cols = [c for c in data[0].keys() if c not in ("ocr_text", "extraction")]
                w = csv.DictWriter(s, fieldnames=cols, extrasaction="ignore")
                w.writeheader()
                w.writerows(data)
            z.writestr(name, "﻿" + s.getvalue())
        for i, r in enumerate(rows):
            if progress:
                progress((i + 1) / max(len(rows), 1), r["file_name"])
            try:
                z.writestr(f"files/{ROOT_FOLDER}/{_rel_path(r)}", backend.download_file(r["storage_path"]))
            except BackendError:
                stats["missing_files"].append(r["storage_path"])
    return buf.getvalue(), stats


def restore_backup_zip(backend, user_id: str, zip_bytes: bytes, progress=None) -> dict:
    """Upload files + records from a backup ZIP. Bills already in the drive are skipped."""
    stats = {"restored": 0, "skipped_existing": 0, "errors": []}
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        try:
            manifest = json.loads(z.read("data/backup.json"))
        except KeyError as exc:
            raise ValueError("This ZIP is not a PurchaseDrive backup (data/backup.json missing).") from exc
        items_by_inv: dict[str, list] = {}
        for it in manifest.get("items", []):
            items_by_inv.setdefault(it["invoice_id"], []).append(it)
        existing_sha = {r.get("file_sha256") for r in backend.list_invoices()}
        id_map: dict[str, str] = {}
        # restore reviewed originals first, so exact copies of them are the ones skipped
        rows = sorted(manifest.get("invoices", []),
                      key=lambda r: (r.get("review_status") != "confirmed",
                                     bool(r.get("is_duplicate")), str(r.get("created_at"))))
        for i, r in enumerate(rows):
            if progress:
                progress((i + 1) / max(len(rows), 1), r.get("file_name", ""))
            if r.get("file_sha256") in existing_sha:
                stats["skipped_existing"] += 1
                continue
            try:
                data = z.read(f"files/{ROOT_FOLDER}/{_rel_path(r)}")
            except KeyError:
                stats["errors"].append(f"File missing in backup: {_rel_path(r)}")
                continue
            folder = r.get("folder_path") or "Inbox"
            full_folder = org.storage_path(user_id, folder, "").rstrip("/")
            name = org.unique_file_name(r["file_name"], backend.list_folder(full_folder))
            path = f"{full_folder}/{name}"
            try:
                backend.upload_file(path, data, r.get("mime_type") or mime_for(name))
                keep = {k: r.get(k) for k in (
                    "original_file_name", "mime_type", "file_size", "file_sha256",
                    "supplier_name", "supplier_gstin", "invoice_number", "invoice_date",
                    "financial_year", "taxable_amount", "cgst_amount", "sgst_amount",
                    "igst_amount", "total_amount", "amount_paid", "payment_status",
                    "review_status", "is_duplicate", "ocr_text", "ocr_confidence",
                    "extraction", "notes")}
                new = backend.insert_invoice({**keep, "user_id": user_id, "file_name": name,
                                              "folder_path": folder, "storage_path": path,
                                              "extraction": keep.get("extraction") or {}})
                id_map[r["id"]] = new["id"]
                backend.replace_items(new["id"], items_by_inv.get(r["id"], []))
                existing_sha.add(r.get("file_sha256"))
                stats["restored"] += 1
            except BackendError as exc:
                stats["errors"].append(f"{_rel_path(r)}: {exc}")
        # re-link duplicates to their new ids
        for r in rows:
            if r.get("duplicate_of") and r["id"] in id_map and r["duplicate_of"] in id_map:
                backend.update_invoice(id_map[r["id"]], {"duplicate_of": id_map[r["duplicate_of"]]})
    return stats
