import streamlit as st

from core import analytics as an
from core import services as sv
from core import ui
from core.backend import BackendError

ui.page_header("📄 Invoice Details & Preview")
backend = ui.get_backend()
rows = ui.get_rows()
if not rows:
    st.info("No bills yet.")
    st.stop()

by_id = {r["id"]: r for r in rows}
ids = sorted(by_id, key=lambda i: by_id[i]["created_at"], reverse=True)
if st.session_state.get("open_invoice") not in by_id:
    st.session_state.open_invoice = ids[0]


def label(i):
    r = by_id[i]
    parts = [r.get("supplier_name") or r["original_file_name"], r.get("invoice_number") or "",
             (r.get("invoice_date") or "")[:10]]
    return " · ".join(p for p in parts if p)


iid = st.selectbox("Choose a bill", ids, format_func=label, key="open_invoice")
try:
    row = backend.get_invoice(iid)
except BackendError as exc:
    st.error(str(exc))
    st.stop()
if not row:
    st.error("This bill no longer exists.")
    ui.refresh_rows()
    st.stop()

badges = ui.payment_badge(row.get("payment_status"))
if row["review_status"] == "pending":
    badges += ui.badge("Needs review", "pending")
if row.get("is_duplicate"):
    badges += ui.badge("Duplicate", "dup")
st.markdown(badges + f' <span class="pd-folder">PurchaseDrive/{row["folder_path"]}/{row["file_name"]}</span>',
            unsafe_allow_html=True)

if row.get("is_duplicate"):
    orig = by_id.get(row.get("duplicate_of"))
    c1, c2 = st.columns([3, 1])
    c1.warning("This bill has the same supplier and invoice number as "
               f"**{orig['file_name'] if orig else 'another saved bill'}**, so it is NOT counted in "
               "totals.")
    if c2.button("It's not a duplicate", width="stretch"):
        sv.set_not_duplicate(backend, row["id"])
        ui.refresh_rows()
        st.rerun()

left, right = st.columns([2, 3], gap="large")
with left:
    ui.show_preview(row, max_pages=5)
    ui.download_button(row, key=f"det_dl_{iid}")
    st.caption(f"Original name: {row['original_file_name']} · "
               f"{(row.get('file_size') or 0) / 1024:,.0f} KB · uploaded "
               f"{str(row['created_at'])[:16].replace('T', ' ')} UTC")
    if st.button("🗑️ Delete this bill", key=f"det_del_{iid}", width="stretch"):
        ui.confirm_delete(row)

with right:
    if row["review_status"] == "confirmed":
        c1, c2, c3 = st.columns(3)
        c1.metric("Invoice total", an.inr(row.get("total_amount")))
        gst = sum(float(row.get(k) or 0) for k in ("cgst_amount", "sgst_amount", "igst_amount"))
        c2.metric("GST", an.inr(gst))
        c3.metric("Financial year", row.get("financial_year") or "—")
        items = backend.get_items(iid)
    else:
        st.info("This bill has not been reviewed yet - check the details and save.")
        items = None
    tab_edit, tab_text = st.tabs(["✏️ Details (edit & save)", "🔤 OCR text"])
    with tab_edit:
        saved = ui.invoice_editor(row, key=f"det_{iid}_{row['updated_at']}", items=items,
                                  submit_label="💾 Save changes")
        if saved:
            st.session_state.flash = f"Saved - PurchaseDrive/{saved['folder_path']}/{saved['file_name']}"
            st.rerun()
    with tab_text:
        if row.get("ocr_text"):
            conf = row.get("ocr_confidence")
            st.caption("Text read from the bill" + (f" (OCR confidence {float(conf):.0f}%)" if conf else ""))
            st.code(row["ocr_text"], language=None)
        else:
            st.caption("OCR has not been run for this bill.")
