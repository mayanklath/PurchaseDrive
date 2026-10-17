"""Folder organisation and safe file naming.

Cloud layout (inside the private bucket):
    <your-user-id>/PurchaseDrive/Inbox/<file>                       (just uploaded, not reviewed)
    <your-user-id>/PurchaseDrive/2026/October/ABC Traders/<file>    (reviewed & saved)
    <your-user-id>/PurchaseDrive/2026/October/Other Suppliers/<file>(supplier unknown)
    <your-user-id>/PurchaseDrive/Undated/ABC Traders/<file>         (date unknown)
"""
from __future__ import annotations

import calendar
import re
import unicodedata
from datetime import date

from core.config import INBOX_FOLDER, ROOT_FOLDER

OTHER_SUPPLIERS = "Other Suppliers"
UNDATED = "Undated"
MONTHS = list(calendar.month_name)[1:]  # January .. December

_UNSAFE = re.compile(r"[^A-Za-z0-9 _\-.()&,]+")


def safe_name(text: str | None, fallback: str = "file", max_len: int = 80) -> str:
    """Make a name that is safe for cloud storage paths (no slashes or odd symbols)."""
    if not text:
        return fallback
    text = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode()
    text = _UNSAFE.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip(" .-_")
    return (text[:max_len].strip(" .-_") or fallback)


def safe_file_name(file_name: str, fallback_stem: str = "invoice") -> str:
    stem, dot, ext = file_name.rpartition(".")
    if not dot:
        stem, ext = file_name, ""
    ext = re.sub(r"[^A-Za-z0-9]", "", ext).lower()[:5]
    stem = safe_name(stem, fallback_stem, max_len=90)
    return f"{stem}.{ext}" if ext else stem


def unique_file_name(file_name: str, existing: set[str] | list[str]) -> str:
    """Return file_name, or 'name (2).ext', 'name (3).ext' ... so nothing is overwritten."""
    existing_lower = {e.lower() for e in existing}
    if file_name.lower() not in existing_lower:
        return file_name
    stem, dot, ext = file_name.rpartition(".")
    if not dot:
        stem, ext = file_name, ""
    n = 2
    while True:
        candidate = f"{stem} ({n}).{ext}" if ext else f"{stem} ({n})"
        if candidate.lower() not in existing_lower:
            return candidate
        n += 1


def clean_supplier_folder(supplier_name: str | None) -> str:
    name = safe_name(supplier_name, fallback="")
    if supplier_name and re.match(r"^\s*m\s*/\s*s\.?\s", supplier_name, re.I):  # "M/s ABC" -> "ABC"
        name = safe_name(re.sub(r"^\s*m\s*/\s*s\.?\s*", "", supplier_name, flags=re.I), "")
    return name or OTHER_SUPPLIERS


def auto_folder(invoice_date: date | None, supplier_name: str | None) -> str:
    """'2026/October/ABC Traders'  (without the PurchaseDrive/ prefix)."""
    supplier = clean_supplier_folder(supplier_name)
    if invoice_date is None:
        return f"{UNDATED}/{supplier}"
    return f"{invoice_date.year}/{MONTHS[invoice_date.month - 1]}/{supplier}"


def normalise_folder(folder: str) -> str:
    """Clean a folder typed by the user, e.g. ' PurchaseDrive/2026//Misc ' -> '2026/Misc'."""
    parts = [safe_name(p, fallback="") for p in folder.replace("\\", "/").split("/")]
    parts = [p for p in parts if p]
    if parts and parts[0].lower() == ROOT_FOLDER.lower():
        parts = parts[1:]
    return "/".join(parts) or OTHER_SUPPLIERS


def storage_path(user_id: str, folder: str, file_name: str) -> str:
    return f"{user_id}/{ROOT_FOLDER}/{folder}/{file_name}"


def inbox_folder(user_id: str) -> str:
    return f"{user_id}/{ROOT_FOLDER}/{INBOX_FOLDER}"


def financial_year(d: date | None) -> str | None:
    """Indian financial year (April-March): 17 Oct 2026 -> '2026-27', 5 Feb 2027 -> '2026-27'."""
    if d is None:
        return None
    start = d.year if d.month >= 4 else d.year - 1
    return f"{start}-{str(start + 1)[-2:]}"


def suggested_file_name(original: str, invoice_date: date | None,
                        invoice_number: str | None, supplier: str | None) -> str:
    """e.g. '2026-10-05_INV-1023_ABC Traders.pdf'. Falls back to the original name."""
    ext = original.rpartition(".")[2].lower() if "." in original else "pdf"
    parts = []
    if invoice_date:
        parts.append(invoice_date.isoformat())
    if invoice_number:
        parts.append(safe_name(invoice_number.replace("/", "-"), "", 40))
    if supplier:
        parts.append(clean_supplier_folder(supplier)[:40])
    parts = [p for p in parts if p and p != OTHER_SUPPLIERS]
    if not parts:
        return safe_file_name(original)
    return safe_file_name("_".join(parts) + "." + ext)
