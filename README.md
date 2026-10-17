# 🧾 PurchaseDrive AI

A private cloud drive for purchase bills. Upload photos or PDFs from your PC or iPhone; the
originals are stored in your private Supabase bucket, OCR reads the GST details, you confirm
anything uncertain, and each bill is filed into `PurchaseDrive/<Year>/<Month>/<Supplier>/`.

**👉 Start here: [SETUP_GUIDE.md](SETUP_GUIDE.md)** (step-by-step for Windows beginners).

## Features
- Email login (Supabase Auth); Row Level Security + per-user storage folders
- Upload JPG / PNG / PDF, many at once, drag & drop, iPhone camera; never overwrites
- Originals saved to the cloud first (Inbox), then filed after review
- OCR: PyMuPDF text layer for digital PDFs, OpenCV + Tesseract for photos/scans
- Extracts supplier, GSTIN (check-digit verified), invoice no/date, HSN items, qty, rate,
  taxable, CGST/SGST/IGST, total, payment status — never invents values; ✅/⚠️/❓ per field
- Auto folders by year/month/supplier; rename and move manually any time
- Search by supplier, invoice no, date/range, amount, GSTIN, financial year, payment status,
  file name; CSV export
- Dashboard: totals, GST, supplier-wise & monthly totals, paid/unpaid, recent uploads;
  duplicates detected (file fingerprint + supplier & invoice number) and never double-counted
- Backup ZIP (all originals + CSV/JSON) and restore
- Optional AI parser hook (`core/ai_parser.py`)

## Run locally
```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .streamlit\secrets.toml.example .streamlit\secrets.toml   # then fill it in
streamlit run app.py
```

## Tests
```powershell
pip install -r requirements-dev.txt
python -m pytest tests -v
```
