from datetime import date

import streamlit as st

from core import services as sv
from core import ui
from core.ai_parser import ai_enabled
from core.backend import BackendError
from core.config import find_tesseract
from core.extractor import gstin_checksum_ok

ui.page_header("⚙️ Settings & Account")
backend = ui.get_backend()
user = ui.current_user()
settings = ui.get_settings()

# ---------------------------------------------------------------------
st.subheader("My business")
with st.form("biz"):
    c1, c2 = st.columns(2)
    name = c1.text_input("Business name", value=settings.get("business_name") or "")
    gst = c2.text_input("My business GSTIN", value=settings.get("my_gstin") or "", max_chars=15,
                        help="Your OWN GSTIN. OCR ignores it so it is never taken as the supplier's.")
    ocr_on = st.toggle("Read new bills automatically with OCR", value=bool(settings.get("ocr_enabled", True)))
    if st.form_submit_button("Save settings", type="primary"):
        g = gst.replace(" ", "").upper()
        if g and not gstin_checksum_ok(g):
            st.error("That GSTIN does not look valid (wrong length or check digit).")
        else:
            new = {"business_name": name.strip() or None, "my_gstin": g or None, "ocr_enabled": ocr_on}
            try:
                backend.save_settings(user.id, new)
                st.session_state.settings = {**settings, **new}
                st.success("Settings saved.")
            except BackendError as exc:
                st.error(str(exc))

# ---------------------------------------------------------------------
st.subheader("OCR engine")
tess = find_tesseract()
if tess:
    try:
        import pytesseract
        pytesseract.pytesseract.tesseract_cmd = tess
        st.success(f"Tesseract OCR {pytesseract.get_tesseract_version()} found at `{tess}`")
    except Exception as exc:
        st.error(f"Tesseract found at `{tess}` but it does not run: {exc}")
else:
    st.warning("Tesseract OCR is not installed - uploads work, but details must be typed by hand. "
               "See SETUP_GUIDE.md → Stage 2.")
st.caption("Advanced AI parser: " + ("**ON** (Claude)" if ai_enabled() else
                                     "off (optional - see core/ai_parser.py for how to enable)"))

# ---------------------------------------------------------------------
st.subheader("Storage")
rows = ui.get_rows()
used = sum(r.get("file_size") or 0 for r in rows)
st.write(f"**{len(rows)}** files · **{used / 1024 / 1024:,.1f} MB** used in your private cloud bucket.")

# ---------------------------------------------------------------------
st.subheader("Backup & restore")
st.caption("A backup is one ZIP file with every original bill (in its folders) plus all details "
           "as CSV and JSON. Keep it on a USB drive or another cloud. Restoring skips bills that "
           "are already in your drive, so it is safe to run twice.")
c1, c2 = st.columns(2)
with c1:
    if st.button("📦 Prepare backup ZIP", width="stretch"):
        bar = st.progress(0.0, text="Collecting files…")
        try:
            data, stats = sv.build_backup_zip(
                backend, progress=lambda p, n: bar.progress(min(p, 1.0), text=f"Adding {n}"))
            st.session_state.backup_zip = data
            st.session_state.backup_stats = stats
        except BackendError as exc:
            st.error(str(exc))
    if "backup_zip" in st.session_state:
        s = st.session_state.backup_stats
        st.success(f"Backup ready: {s['invoices']} bills, {s['items']} line items.")
        if s["missing_files"]:
            st.warning(f"{len(s['missing_files'])} file(s) could not be read from storage.")
        st.download_button("⬇️ Download backup ZIP", data=st.session_state.backup_zip,
                           file_name=f"PurchaseDrive_backup_{date.today().isoformat()}.zip",
                           mime="application/zip", width="stretch", type="primary")
with c2:
    up = st.file_uploader("Restore from a backup ZIP", type=["zip"], key="restore_zip")
    if up and st.button("♻️ Restore this backup", width="stretch"):
        bar = st.progress(0.0, text="Restoring…")
        try:
            stats = sv.restore_backup_zip(
                backend, user.id, up.getvalue(),
                progress=lambda p, n: bar.progress(min(p, 1.0), text=f"Restoring {n}"))
            st.success(f"Restored {stats['restored']} bill(s); skipped "
                       f"{stats['skipped_existing']} already in your drive.")
            for e in stats["errors"]:
                st.error(e)
            ui.refresh_rows()
        except (ValueError, BackendError) as exc:
            st.error(str(exc))

# ---------------------------------------------------------------------
st.subheader("Account")
st.write(f"Logged in as **{user.email}**")
with st.form("pw"):
    p1 = st.text_input("New password (min. 8 characters)", type="password")
    p2 = st.text_input("Repeat new password", type="password")
    if st.form_submit_button("Change password"):
        if len(p1) < 8:
            st.error("Password must be at least 8 characters.")
        elif p1 != p2:
            st.error("The two passwords are different.")
        else:
            try:
                backend.change_password(p1)
                st.success("Password changed.")
            except BackendError as exc:
                st.error(str(exc))
if st.button("Log out"):
    ui.logout()
    st.rerun()
