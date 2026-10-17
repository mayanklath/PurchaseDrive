"""Shared Streamlit helpers: login state, cached data, previews, tables, the invoice editor."""
from __future__ import annotations

import os
import time
from datetime import date

import pandas as pd
import streamlit as st

from core import analytics as an
from core import organizer as org
from core import services as sv
from core.backend import BackendError, MemoryBackend, SupabaseBackend
from core.config import APP_NAME, looks_like_service_key, supabase_credentials
from core.extractor import extraction_from_dict

PAGES = {
    "dashboard": "views/dashboard.py",
    "upload": "views/upload.py",
    "all": "views/all_bills.py",
    "suppliers": "views/suppliers.py",
    "folders": "views/folders.py",
    "search": "views/search.py",
    "details": "views/details.py",
    "settings": "views/settings.py",
}
FIELD_LABELS = {
    "supplier_name": "Supplier name", "supplier_gstin": "Supplier GSTIN",
    "invoice_number": "Invoice number", "invoice_date": "Invoice date",
    "taxable_amount": "Taxable amount", "cgst_amount": "CGST", "sgst_amount": "SGST",
    "igst_amount": "IGST", "total_amount": "Invoice total", "payment_status": "Payment status",
}
CONF_BADGE = {"high": "✅", "medium": "⚠️", "low": "⚠️", "missing": "❓"}


# =====================================================================
#  Look & feel
# =====================================================================
def inject_css() -> None:
    st.markdown("""
<style>
  #MainMenu, .stDeployButton, [data-testid="stToolbarActions"] {visibility: hidden;}
  .block-container {padding-top: 2.2rem; padding-bottom: 3rem; max-width: 1250px;}
  [data-testid="stMetric"] {background: var(--secondary-background-color);
      border: 1px solid rgba(128,128,128,.18); border-radius: 12px; padding: 14px 16px;}
  [data-testid="stMetricValue"] {font-size: 1.45rem;}
  .pd-badge {display:inline-block; padding:2px 10px; border-radius:999px; font-size:.8rem;
      font-weight:600; margin-right:4px;}
  .pd-paid {background:#dcfce7; color:#166534;} .pd-unpaid {background:#fee2e2; color:#991b1b;}
  .pd-partially_paid {background:#fef3c7; color:#92400e;} .pd-unknown {background:#e5e7eb; color:#374151;}
  .pd-dup {background:#ede9fe; color:#5b21b6;} .pd-pending {background:#dbeafe; color:#1e40af;}
  .pd-folder {font-family: ui-monospace, Menlo, Consolas, monospace; font-size:.85rem;
      background: var(--secondary-background-color); padding:2px 8px; border-radius:6px;}
  @media (max-width: 640px) {
    .block-container {padding-left: .8rem; padding-right: .8rem; padding-top: 1rem;}
    [data-testid="stMetricValue"] {font-size: 1.15rem;}
    h1 {font-size: 1.6rem !important;}
  }
</style>""", unsafe_allow_html=True)


def badge(text: str, kind: str) -> str:
    return f'<span class="pd-badge pd-{kind}">{text}</span>'


def payment_badge(status: str | None) -> str:
    s = status or "unknown"
    return badge(sv.PAYMENT_LABELS.get(s, s), s)


# =====================================================================
#  Session / login
# =====================================================================
def get_backend():
    if "backend" not in st.session_state:
        if os.environ.get("PD_TEST_BACKEND") == "memory":
            st.session_state.backend = MemoryBackend()
        else:
            url, key = supabase_credentials()
            if not url or not key:
                st.error("Supabase is not configured yet.")
                st.markdown(
                    "Create the file **`.streamlit/secrets.toml`** in the project folder with:\n"
                    "```toml\nSUPABASE_URL = \"https://YOUR-PROJECT.supabase.co\"\n"
                    "SUPABASE_ANON_KEY = \"your anon / publishable key\"\n```\n"
                    "Then restart the app. See **SETUP_GUIDE.md**, step 4.")
                st.stop()
            if looks_like_service_key(key):
                st.error("Security stop: the key in secrets.toml is a SERVICE-ROLE / SECRET key. "
                         "Never use it in this app. Replace it with the anon / publishable key.")
                st.stop()
            st.session_state.backend = SupabaseBackend(url, key)
    return st.session_state.backend


def current_user():
    return get_backend().current_user()


def logout() -> None:
    get_backend().sign_out()
    for k in list(st.session_state.keys()):
        del st.session_state[k]


# =====================================================================
#  Data cache (per browser session)
# =====================================================================
ROWS_TTL = 60  # seconds - pick up uploads made from another device


def get_rows(force: bool = False) -> list[dict]:
    stamp = st.session_state.get("rows_at", 0)
    if force or "rows" not in st.session_state or time.time() - stamp > ROWS_TTL:
        try:
            st.session_state.rows = get_backend().list_invoices()
        except BackendError as exc:
            st.error(f"Could not load your invoices: {exc}")
            st.stop()
        st.session_state.rows_at = time.time()
    return st.session_state.rows


def refresh_rows() -> None:
    st.session_state.pop("rows", None)


def get_df() -> pd.DataFrame:
    return an.to_df(get_rows())


def get_settings() -> dict:
    if "settings" not in st.session_state:
        try:
            st.session_state.settings = get_backend().get_settings() or {}
        except BackendError:
            st.session_state.settings = {}
    return st.session_state.settings


def file_bytes(storage_path: str) -> bytes:
    cache = st.session_state.setdefault("file_cache", {})
    if storage_path not in cache:
        if len(cache) > 15:
            cache.pop(next(iter(cache)))
        cache[storage_path] = get_backend().download_file(storage_path)
    return cache[storage_path]


def forget_file(storage_path: str) -> None:
    st.session_state.get("file_cache", {}).pop(storage_path, None)


def open_invoice(invoice_id: str) -> None:
    st.session_state.open_invoice = invoice_id
    st.switch_page(PAGES["details"])


# =====================================================================
#  Preview & download
# =====================================================================
def show_preview(row: dict, data: bytes | None = None, max_pages: int = 3) -> None:
    from core.ocr import pdf_page_count, pdf_preview_images

    try:
        data = data or file_bytes(row["storage_path"])
    except BackendError as exc:
        st.error(f"Could not load the file: {exc}")
        return
    if row.get("mime_type") == "application/pdf":
        try:
            pages = pdf_preview_images(data, max_pages=max_pages)
            n = pdf_page_count(data)
        except Exception as exc:
            st.warning(f"Preview not available ({exc}). You can still download the file.")
            return
        for i, png in enumerate(pages, 1):
            st.image(png, caption=f"Page {i} of {n}", width="stretch")
        if n > max_pages:
            st.caption(f"Showing the first {max_pages} pages - download to see all {n}.")
    else:
        st.image(data, width="stretch")


def download_button(row: dict, key: str, label: str = "⬇️ Download original") -> None:
    try:
        data = file_bytes(row["storage_path"])
    except BackendError as exc:
        st.error(str(exc))
        return
    st.download_button(label, data=data, file_name=row.get("original_file_name") or row["file_name"],
                       mime=row.get("mime_type") or "application/octet-stream", key=key,
                       width="stretch")


# =====================================================================
#  Tables
# =====================================================================
def invoice_table(df: pd.DataFrame, key: str, height: int | str = "auto",
                  show_folder: bool = True) -> None:
    """Pretty table; select a row and press Open to see the bill."""
    if df.empty:
        st.info("No invoices here yet.")
        return
    view = pd.DataFrame({
        "Supplier": df["supplier_name"].fillna("—"),
        "Invoice No": df["invoice_number"].fillna("—"),
        "Date": df["invoice_date"],
        "Total (₹)": df["total_amount"],
        "GST (₹)": df["gst_total"],
        "Payment": df["payment_status"].map(sv.PAYMENT_LABELS).fillna("Not set"),
        "Status": [("Duplicate" if d else ("Saved" if r == "confirmed" else "Needs review"))
                   for d, r in zip(df["is_duplicate"], df["review_status"])],
        "Folder": df["folder_display"],
        "File": df["file_name"],
    })
    if not show_folder:
        view = view.drop(columns=["Folder"])
    event = st.dataframe(
        view, key=key, hide_index=True, width="stretch", height=height,
        on_select="rerun", selection_mode="single-row",
        column_config={
            "Date": st.column_config.DateColumn(format="DD-MM-YYYY"),
            "Total (₹)": st.column_config.NumberColumn(format="₹ %.2f"),
            "GST (₹)": st.column_config.NumberColumn(format="₹ %.2f"),
        })
    sel = event.selection.rows if event and event.selection else []
    if sel:
        r = df.iloc[sel[0]]
        label = f"📄 Open {r['supplier_name'] or r['file_name']} {r['invoice_number'] or ''}".strip()
        if st.button(label, key=f"{key}_open", type="primary"):
            open_invoice(r["id"])
    else:
        st.caption("Tip: tick a row to open, preview or download that bill.")


def csv_button(df: pd.DataFrame, name: str, key: str) -> None:
    st.download_button("📥 Export CSV (Excel)", data=an.export_csv(df),
                       file_name=f"{name}_{date.today().isoformat()}.csv", mime="text/csv",
                       key=key, disabled=df.empty)


# =====================================================================
#  Invoice editor (used for reviewing new uploads AND editing saved bills)
# =====================================================================
def _num(v):
    try:
        return None if v is None or v != v else float(v)
    except (TypeError, ValueError):
        return None


def invoice_editor(row: dict, key: str, items: list[dict] | None = None,
                   submit_label: str = "💾 Save invoice") -> dict | None:
    """Render the details form. Returns the saved row when the user saves, else None."""
    full = row
    if "extraction" not in row:
        full = get_backend().get_invoice(row["id"]) or row
    ex = extraction_from_dict(full.get("extraction") or {})
    pending = row.get("review_status") == "pending"
    rows = get_rows()

    def initial(f):
        if not pending and row.get(f) is not None:
            return row.get(f)
        if pending:
            return ex.fields[f].value
        return row.get(f)

    # suggestions for a pending bill
    sup = initial("supplier_name")
    if pending:
        known = sv.known_supplier_name(rows, initial("supplier_gstin"), sup)
        if known and known != sup:
            st.caption(f"ℹ️ Using the supplier name you saved before: **{known}**")
            sup = known

    if pending and full.get("extraction"):
        unsure = [FIELD_LABELS[f] for f in ex.uncertain_fields()]
        if unsure:
            st.warning("Please check these fields before saving: **" + ", ".join(unsure) + "**  \n"
                       "✅ = read clearly · ⚠️ = please check · ❓ = not found on the bill "
                       "(left empty - nothing is guessed)")
        else:
            st.success("All fields were read clearly. Please give them a quick look and save.")
        for n in ex.notes:
            st.info(n)
    elif pending:
        st.info("Enter the invoice details below (OCR was not run for this file).")

    def lab(f):
        if not pending or not full.get("extraction"):
            return FIELD_LABELS[f]
        if ex.not_applicable(f):
            return FIELD_LABELS[f]
        return f"{FIELD_LABELS[f]} {CONF_BADGE[ex.fields[f].confidence]}"

    def hlp(f):
        src = ex.fields[f].source if pending else ""
        return f"Read from: “{src}”" if src else None

    d0 = initial("invoice_date")
    if isinstance(d0, str):
        try:
            d0 = date.fromisoformat(d0[:10])
        except ValueError:
            d0 = None
    status0 = initial("payment_status") or "unknown"

    with st.form(key=f"{key}_form", border=True):
        c1, c2 = st.columns(2)
        supplier = c1.text_input(lab("supplier_name"), value=sup or "", help=hlp("supplier_name"))
        gstin = c2.text_input(lab("supplier_gstin"), value=initial("supplier_gstin") or "",
                              max_chars=15, help=hlp("supplier_gstin"))
        c1, c2 = st.columns(2)
        inv_no = c1.text_input(lab("invoice_number"), value=initial("invoice_number") or "",
                               help=hlp("invoice_number"))
        inv_date = c2.date_input(lab("invoice_date"), value=d0, format="DD/MM/YYYY",
                                 min_value=date(2000, 1, 1), max_value=date(2100, 1, 1),
                                 help=hlp("invoice_date"))
        c1, c2 = st.columns(2)
        taxable = c1.number_input(lab("taxable_amount"), value=_num(initial("taxable_amount")),
                                  min_value=0.0, step=1.0, format="%.2f", help=hlp("taxable_amount"))
        total = c2.number_input(lab("total_amount"), value=_num(initial("total_amount")),
                                min_value=0.0, step=1.0, format="%.2f", help=hlp("total_amount"))
        c1, c2, c3 = st.columns(3)
        cgst = c1.number_input(lab("cgst_amount"), value=_num(initial("cgst_amount")),
                               min_value=0.0, step=1.0, format="%.2f", help=hlp("cgst_amount"))
        sgst = c2.number_input(lab("sgst_amount"), value=_num(initial("sgst_amount")),
                               min_value=0.0, step=1.0, format="%.2f", help=hlp("sgst_amount"))
        igst = c3.number_input(lab("igst_amount"), value=_num(initial("igst_amount")),
                               min_value=0.0, step=1.0, format="%.2f", help=hlp("igst_amount"))
        c1, c2 = st.columns(2)
        status = c1.selectbox(lab("payment_status"), sv.PAYMENT_STATUSES,
                              index=sv.PAYMENT_STATUSES.index(status0),
                              format_func=lambda s: sv.PAYMENT_LABELS[s])
        paid = c2.number_input("Amount paid (for partially paid)", value=_num(row.get("amount_paid")),
                               min_value=0.0, step=1.0, format="%.2f")

        st.markdown("**Line items** (HSN, quantity, rate) - add, edit or delete rows")
        base_items = items if items is not None else (ex.items if pending else [])
        items_df = pd.DataFrame(base_items or [], columns=[
            "description", "hsn_code", "quantity", "unit", "rate", "taxable_amount", "gst_rate"])
        items_df = items_df[["description", "hsn_code", "quantity", "unit", "rate",
                             "taxable_amount", "gst_rate"]]
        edited = st.data_editor(
            items_df, key=f"{key}_items", num_rows="dynamic", width="stretch",
            hide_index=True,
            column_config={
                "description": st.column_config.TextColumn("Description", width="large"),
                "hsn_code": st.column_config.TextColumn("HSN"),
                "quantity": st.column_config.NumberColumn("Qty", format="%.3f"),
                "unit": st.column_config.TextColumn("Unit"),
                "rate": st.column_config.NumberColumn("Rate ₹", format="%.2f"),
                "taxable_amount": st.column_config.NumberColumn("Amount ₹", format="%.2f"),
                "gst_rate": st.column_config.NumberColumn("GST %", format="%.2f"),
            })

        st.markdown("**File & folder**")
        auto_name = org.suggested_file_name(row["original_file_name"], inv_date, inv_no or None,
                                            supplier or None) if pending else row["file_name"]
        c1, c2 = st.columns(2)
        file_name = c1.text_input("File name", value=auto_name,
                                  help="Saved with this name. A number is added automatically "
                                       "if the name already exists - nothing is overwritten.")
        current_folder = "" if pending else row.get("folder_path", "")
        auto_folder = org.auto_folder(d0, sup)
        folder_mode = c2.radio("Folder", ["Automatic (Year / Month / Supplier)", "Choose my own"],
                               index=0 if pending or current_folder == auto_folder else 1,
                               horizontal=True)
        custom_folder = st.text_input("Own folder (only used with 'Choose my own')",
                                      value=current_folder or auto_folder,
                                      help="Example: 2026/October/ABC Traders  or  Misc/Old bills")
        notes = st.text_area("Notes", value=row.get("notes") or "", height=68)
        submitted = st.form_submit_button(submit_label, type="primary", width="stretch")

    if not submitted:
        return None
    form = {"supplier_name": supplier, "supplier_gstin": gstin, "invoice_number": inv_no,
            "invoice_date": inv_date, "taxable_amount": taxable, "cgst_amount": cgst,
            "sgst_amount": sgst, "igst_amount": igst, "total_amount": total,
            "payment_status": status, "amount_paid": paid, "notes": notes}
    if gstin.strip():
        from core.extractor import gstin_checksum_ok
        if not gstin_checksum_ok(gstin.strip().upper()):
            st.warning("Note: this GSTIN's check digit is not valid - it may be mistyped. "
                       "Saved anyway.")
    folder = custom_folder if folder_mode.startswith("Choose") else None
    try:
        saved = sv.save_invoice(get_backend(), current_user().id, row, form,
                                edited.to_dict("records"), file_name=file_name, folder=folder,
                                all_rows=rows)
    except (ValueError, BackendError) as exc:
        st.error(f"Not saved: {exc}")
        return None
    forget_file(row["storage_path"])
    refresh_rows()
    return saved


# =====================================================================
#  Delete with confirmation
# =====================================================================
@st.dialog("Delete this bill?")
def confirm_delete(row: dict) -> None:
    st.write(f"**{row.get('supplier_name') or row['file_name']}** "
             f"{row.get('invoice_number') or ''}")
    st.write("This permanently deletes the original file from your cloud drive and all of its "
             "details. It cannot be undone (unless you have a backup).")
    typed = st.text_input("Type DELETE to confirm")
    c1, c2 = st.columns(2)
    if c1.button("Cancel", width="stretch"):
        st.rerun()
    if c2.button("🗑️ Delete forever", type="primary", disabled=typed.strip().upper() != "DELETE",
                 width="stretch"):
        try:
            sv.delete_invoice(get_backend(), row)
        except BackendError as exc:
            st.error(str(exc))
            return
        forget_file(row["storage_path"])
        refresh_rows()
        st.session_state.pop("open_invoice", None)
        st.session_state.flash = f"Deleted {row['file_name']}."
        st.rerun()


def flash() -> None:
    msg = st.session_state.pop("flash", None)
    if msg:
        st.toast(msg, icon="✅")


def page_header(title: str, subtitle: str | None = None) -> None:
    st.title(title)
    if subtitle:
        st.caption(subtitle)


__all__ = ["APP_NAME"]
