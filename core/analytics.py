"""Dashboard numbers, search filters and CSV export (Pandas)."""
from __future__ import annotations

from datetime import date

import pandas as pd

from core.organizer import MONTHS, ROOT_FOLDER

AMOUNT_COLS = ["taxable_amount", "cgst_amount", "sgst_amount", "igst_amount", "total_amount",
               "amount_paid"]
EXPORT_COLS = {
    "supplier_name": "Supplier", "supplier_gstin": "Supplier GSTIN",
    "invoice_number": "Invoice No", "invoice_date": "Invoice Date",
    "financial_year": "Financial Year", "taxable_amount": "Taxable Amount",
    "cgst_amount": "CGST", "sgst_amount": "SGST", "igst_amount": "IGST",
    "total_amount": "Invoice Total", "amount_paid": "Amount Paid",
    "payment_status": "Payment Status", "folder_display": "Folder", "file_name": "File Name",
    "original_file_name": "Original File Name", "review_status": "Review",
    "is_duplicate": "Duplicate", "created_at": "Uploaded At", "notes": "Notes",
}


def to_df(rows: list[dict]) -> pd.DataFrame:
    cols = ["id", "created_at", "original_file_name", "file_name", "storage_path", "folder_path",
            "mime_type", "supplier_name", "supplier_gstin", "invoice_number", "invoice_date",
            "financial_year", "payment_status", "review_status", "is_duplicate", "duplicate_of",
            "notes"] + AMOUNT_COLS
    df = pd.DataFrame(rows or [], columns=None if rows else cols)
    for c in cols:
        if c not in df.columns:
            df[c] = None
    for c in AMOUNT_COLS:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["invoice_date"] = pd.to_datetime(df["invoice_date"], errors="coerce")
    df["created_at"] = pd.to_datetime(df["created_at"], errors="coerce", utc=True)
    df["is_duplicate"] = df["is_duplicate"].fillna(False).astype(bool)
    df["gst_total"] = df[["cgst_amount", "sgst_amount", "igst_amount"]].fillna(0).sum(axis=1)
    df["folder_display"] = ROOT_FOLDER + "/" + df["folder_path"].fillna("Inbox")
    df["balance_due"] = (df["total_amount"].fillna(0) - df["amount_paid"].fillna(0)).clip(lower=0)
    return df


def counted(df: pd.DataFrame) -> pd.DataFrame:
    """Invoices that count in totals: reviewed & saved, and not a duplicate copy."""
    return df[(df["review_status"] == "confirmed") & (~df["is_duplicate"])]


def dashboard_metrics(df: pd.DataFrame) -> dict:
    c = counted(df)
    unpaid = c[c["payment_status"].isin(["unpaid", "partially_paid"])]
    return {
        "invoices": int(len(c)),
        "purchase_total": float(c["total_amount"].fillna(0).sum()),
        "taxable_total": float(c["taxable_amount"].fillna(0).sum()),
        "gst_total": float(c["gst_total"].sum()),
        "pending_review": int((df["review_status"] == "pending").sum()),
        "duplicates": int(((df["review_status"] == "confirmed") & df["is_duplicate"]).sum()),
        "paid_count": int((c["payment_status"] == "paid").sum()),
        "paid_amount": float(c.loc[c["payment_status"] == "paid", "total_amount"].fillna(0).sum()),
        "unpaid_count": int(len(unpaid)),
        "balance_due": float(unpaid["balance_due"].sum()),
        "unknown_payment": int((c["payment_status"] == "unknown").sum()),
    }


def supplier_totals(df: pd.DataFrame) -> pd.DataFrame:
    c = counted(df).copy()
    c["supplier"] = c["supplier_name"].fillna("Other Suppliers")
    g = c.groupby("supplier").agg(invoices=("id", "count"),
                                  taxable=("taxable_amount", "sum"),
                                  gst=("gst_total", "sum"),
                                  total=("total_amount", "sum"),
                                  due=("balance_due", "sum"))
    return g.sort_values("total", ascending=False).reset_index()


def monthly_totals(df: pd.DataFrame) -> pd.DataFrame:
    c = counted(df).dropna(subset=["invoice_date"]).copy()
    if c.empty:
        return pd.DataFrame(columns=["month", "total", "gst", "invoices"])
    c["month"] = c["invoice_date"].dt.to_period("M").astype(str)
    g = c.groupby("month").agg(total=("total_amount", "sum"), gst=("gst_total", "sum"),
                               invoices=("id", "count")).reset_index()
    return g.sort_values("month")


def financial_years(df: pd.DataFrame) -> list[str]:
    return sorted([fy for fy in df["financial_year"].dropna().unique()], reverse=True)


def filter_invoices(df: pd.DataFrame, *, text: str = "", supplier: str = "",
                    invoice_number: str = "", gstin: str = "", file_name: str = "",
                    date_from: date | None = None, date_to: date | None = None,
                    amount_min: float | None = None, amount_max: float | None = None,
                    financial_year: str | None = None, payment: list[str] | None = None,
                    review: str | None = None, include_duplicates: bool = True) -> pd.DataFrame:
    out = df
    def has(col, needle):
        return out[col].fillna("").astype(str).str.contains(needle.strip(), case=False, regex=False)
    if text.strip():
        t = text.strip()
        mask = False
        for col in ("supplier_name", "invoice_number", "supplier_gstin", "original_file_name",
                    "file_name", "notes", "folder_path"):
            mask = mask | out[col].fillna("").astype(str).str.contains(t, case=False, regex=False)
        try:
            mask = mask | ((out["total_amount"] - float(t.replace(",", ""))).abs() < 0.5)
        except ValueError:
            pass
        out = out[mask]
    if supplier.strip():
        out = out[has("supplier_name", supplier)]
    if invoice_number.strip():
        out = out[has("invoice_number", invoice_number)]
    if gstin.strip():
        out = out[has("supplier_gstin", gstin.replace(" ", ""))]
    if file_name.strip():
        out = out[has("original_file_name", file_name) | has("file_name", file_name)]
    if date_from:
        out = out[out["invoice_date"] >= pd.Timestamp(date_from)]
    if date_to:
        out = out[out["invoice_date"] <= pd.Timestamp(date_to)]
    if amount_min is not None:
        out = out[out["total_amount"] >= amount_min]
    if amount_max is not None:
        out = out[out["total_amount"] <= amount_max]
    if financial_year:
        out = out[out["financial_year"] == financial_year]
    if payment:
        out = out[out["payment_status"].isin(payment)]
    if review:
        out = out[out["review_status"] == review]
    if not include_duplicates:
        out = out[~out["is_duplicate"]]
    return out


def export_csv(df: pd.DataFrame) -> bytes:
    cols = [c for c in EXPORT_COLS if c in df.columns]
    out = df[cols].rename(columns=EXPORT_COLS).copy()
    if "Invoice Date" in out:
        out["Invoice Date"] = out["Invoice Date"].dt.strftime("%d-%m-%Y")
    if "Uploaded At" in out:
        out["Uploaded At"] = out["Uploaded At"].dt.tz_convert("Asia/Kolkata").dt.strftime("%d-%m-%Y %H:%M")
    return out.to_csv(index=False).encode("utf-8-sig")  # opens correctly in Excel


def year_month_tree(df: pd.DataFrame) -> dict:
    """{year: {month_name: {supplier: [rows]}}} for the folder pages."""
    tree: dict = {}
    for _, r in df.iterrows():
        parts = (r["folder_path"] or "Inbox").split("/")
        if len(parts) >= 3 and parts[0].isdigit() and parts[1] in MONTHS:
            y, m, s = parts[0], parts[1], "/".join(parts[2:])
        elif len(parts) >= 2:
            y, m, s = parts[0], "", "/".join(parts[1:])
        else:
            y, m, s = parts[0], "", ""
        tree.setdefault(y, {}).setdefault(m, {}).setdefault(s, []).append(r)
    return tree


def inr(v) -> str:
    """Indian number format: 1234567.5 -> '₹12,34,567.50'."""
    if v is None or (isinstance(v, float) and v != v):
        return "—"
    neg = v < 0
    v = abs(float(v))
    whole, frac = f"{v:.2f}".split(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join(groups) + "," + tail
    return ("-" if neg else "") + "₹" + whole + "." + frac
