import streamlit as st

from core import services as sv
from core import ui
from core.config import ALLOWED_TYPES, MAX_FILE_MB

ui.page_header("📤 Upload Purchase Bills",
               "Step 1: upload - the original is saved in your private cloud immediately. "
               "Step 2: check the details and save - the bill is filed into its folder.")
backend = ui.get_backend()
user = ui.current_user()
settings = ui.get_settings()

if "ocr_ok" not in st.session_state:
    from core.ocr import tesseract_available
    st.session_state.ocr_ok = tesseract_available()
ocr_ok = st.session_state.ocr_ok
st.session_state.setdefault("uploader_key", 0)
st.session_state.setdefault("dup_queue", {})

# ---------------------------------------------------------------------
#  1. Upload
# ---------------------------------------------------------------------
st.subheader("1. Upload")
files = st.file_uploader(
    f"Drag & drop bills here, or tap to choose. JPG, PNG or PDF, up to {MAX_FILE_MB} MB each. "
    "On iPhone you can pick 'Take Photo'.",
    type=ALLOWED_TYPES, accept_multiple_files=True, key=f"uploader_{st.session_state.uploader_key}")
use_ocr = st.toggle("Read invoice details automatically (OCR)",
                    value=bool(settings.get("ocr_enabled", True)) and ocr_ok, disabled=not ocr_ok)
if not ocr_ok:
    st.caption("ℹ️ OCR is not available because Tesseract is not installed (Stage 2 of the setup "
               "guide). You can still upload bills and type the details yourself.")

if files and st.button(f"☁️ Upload {len(files)} file(s) to my cloud drive", type="primary",
                       width="stretch"):
    bar = st.progress(0.0, text="Starting…")
    results = []
    for i, f in enumerate(files):
        data = f.getvalue()
        bar.progress(i / len(files), text=f"Uploading {f.name}…")
        out = sv.upload_to_inbox(backend, user.id, f.name, data)
        if out.status == "uploaded" and use_ocr:
            bar.progress((i + 0.5) / len(files), text=f"Reading {f.name} (OCR)…")
            _ex, err = sv.extract_invoice(backend, out.row, data, settings)
            if err:
                out.message += f" OCR: {err}"
        if out.status == "exact_duplicate":
            st.session_state.dup_queue[f.name] = data
        results.append((out.status, out.message))
    bar.progress(1.0, text="Done")
    st.session_state.upload_results = results
    st.session_state.uploader_key += 1          # clears the upload box
    ui.refresh_rows()
    st.rerun()

for status, msg in st.session_state.pop("upload_results", []):
    {"uploaded": st.success, "exact_duplicate": st.warning}.get(status, st.error)(
        ("✅ " if status == "uploaded" else "") + msg)

for name, data in list(st.session_state.dup_queue.items()):
    with st.container(border=True):
        st.warning(f"**{name}** is exactly the same file as one already in your drive.")
        c1, c2 = st.columns(2)
        if c1.button("Upload anyway", key=f"dupup_{name}", width="stretch"):
            out = sv.upload_to_inbox(backend, user.id, name, data, allow_duplicate=True)
            if out.status == "uploaded" and use_ocr:
                sv.extract_invoice(backend, out.row, data, settings)
            st.session_state.dup_queue.pop(name)
            ui.refresh_rows()
            st.rerun()
        if c2.button("Skip it", key=f"dupskip_{name}", width="stretch"):
            st.session_state.dup_queue.pop(name)
            st.rerun()

# ---------------------------------------------------------------------
#  2. Review & save
# ---------------------------------------------------------------------
st.divider()
pending = sorted([r for r in ui.get_rows() if r.get("review_status") == "pending"],
                 key=lambda r: r["created_at"])
st.subheader(f"2. Check & save ({len(pending)} waiting)")
if not pending:
    st.success("Nothing waiting for review. 🎉")
    st.stop()

ids = [r["id"] for r in pending]
if st.session_state.get("review_id") not in ids:
    st.session_state.review_id = ids[0]
labels = {r["id"]: f"{i}. {r['original_file_name']}" for i, r in enumerate(pending, 1)}
rid = st.selectbox("Bill to review", ids, format_func=labels.get, key="review_id")
row = next(r for r in pending if r["id"] == rid)

left, right = st.columns([2, 3], gap="large")
with left:
    ui.show_preview(row, max_pages=2)
    ui.download_button(row, key=f"dl_{rid}")
    c1, c2 = st.columns(2)
    if c1.button("🔁 Read again (OCR)", key=f"reocr_{rid}", width="stretch", disabled=not ocr_ok):
        with st.spinner("Reading the bill…"):
            _ex, err = sv.extract_invoice(backend, row, ui.file_bytes(row["storage_path"]), settings)
        if err:
            st.error(err)
        else:
            st.session_state[f"ver_{rid}"] = st.session_state.get(f"ver_{rid}", 0) + 1
            st.rerun()
    if c2.button("🗑️ Delete", key=f"del_{rid}", width="stretch"):
        ui.confirm_delete(row)
with right:
    saved = ui.invoice_editor(row, key=f"rev_{rid}_{st.session_state.get(f'ver_{rid}', 0)}",
                              submit_label="💾 Save & file this bill")
    if saved:
        msg = f"Saved to PurchaseDrive/{saved['folder_path']}/{saved['file_name']}"
        if saved.get("is_duplicate"):
            msg += " - marked as DUPLICATE (same supplier & invoice number already saved), " \
                   "so it is not counted twice."
        st.session_state.flash = msg
        st.session_state.pop("review_id", None)
        st.rerun()
