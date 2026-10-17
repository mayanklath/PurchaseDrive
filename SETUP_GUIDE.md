# PurchaseDrive AI — Complete Setup Guide (Windows, beginner friendly)

This guide takes you from nothing to a working private cloud drive for your purchase bills
that you can open on your PC and your iPhone. Follow the steps **in order**. Nothing needs
coding knowledge — only copy & paste.

* **Stage 1** — secure login, upload, cloud storage, search, download (works without OCR)
* **Stage 2** — automatic reading of bills (OCR) and automatic folder filing
* **Stage 3** — put it online and use it from your iPhone

Time needed: about 45–60 minutes the first time.

---

## 0. How it works (architecture)

```
  Your PC / iPhone (any web browser)
              │  https
              ▼
  ┌──────────────────────────────────────────────┐
  │  PurchaseDrive AI  (Python + Streamlit app)   │
  │   app.py ─ login + menu                       │
  │   views/ ─ the 8 pages                        │
  │   core/  ─ OCR, extraction, folders, search   │
  │            Tesseract + OpenCV + PyMuPDF       │
  └──────────────┬───────────────────────────────┘
                 │ logged-in user's token only (public key, never the service key)
                 ▼
  ┌──────────────────────────────────────────────┐
  │  Supabase (your cloud account)                │
  │   Auth     ─ email + password login           │
  │   Postgres ─ invoices, invoice_items,         │
  │              user_settings   (Row Level       │
  │              Security: each user sees only    │
  │              their own rows)                  │
  │   Storage  ─ PRIVATE bucket "invoices"        │
  │      <your-id>/PurchaseDrive/Inbox/...        │
  │      <your-id>/PurchaseDrive/2026/October/    │
  │                       ABC Traders/bill.pdf    │
  └──────────────────────────────────────────────┘
```

What happens when you upload a bill:

1. The **original file goes straight into your private cloud bucket** (folder `Inbox`) and a
   database record is created. Even if you close the browser now, nothing is lost.
2. OCR reads the bill. Computer-made PDFs are read directly (100 % exact); photos and scanned
   PDFs are cleaned with OpenCV (rotation, shadows, skew) and read with Tesseract.
3. The extractor finds supplier, GSTIN (verified with the GSTIN check digit), invoice number,
   date, amounts, HSN line items and payment status. It **never invents values**: anything not
   clearly printed stays empty. Each field gets ✅ (read clearly), ⚠️ (please check) or ❓ (not found).
   Amounts are cross-checked: taxable + GST must equal the total.
4. You check the highlighted fields and press **Save & file this bill**. The file is then moved to
   `PurchaseDrive/<Year>/<Month>/<Supplier>/` and renamed, e.g.
   `2026-10-05_INV-1023_ABC Traders.pdf`. You can change the name or folder.
5. If the same supplier + invoice number already exists, the bill is marked **Duplicate** and is
   never counted twice in the dashboard. Uploading the exact same file twice is stopped at once.

## 1. Project folder structure

```
PurchaseDrive\
├── app.py                         ← start file (login + navigation)
├── requirements.txt               ← Python packages
├── requirements-dev.txt           ← extra packages for the automated tests
├── packages.txt                   ← Tesseract for Streamlit Cloud (Linux)
├── SETUP_GUIDE.md                 ← this guide
├── README.md
├── .gitignore                     ← keeps secrets.toml out of GitHub
├── .streamlit\
│   ├── config.toml                ← look & upload limit
│   ├── secrets.toml.example       ← template
│   └── secrets.toml               ← YOU create this (step 4) - never share it
├── core\
│   ├── config.py                  ← settings, Tesseract finder, service-key guard
│   ├── backend.py                 ← Supabase Auth / Storage / Database calls
│   ├── services.py                ← upload, save, duplicates, delete, backup/restore
│   ├── ocr.py                     ← OpenCV clean-up, Tesseract, PyMuPDF, previews
│   ├── extractor.py               ← reads GST invoice fields from OCR text
│   ├── ai_parser.py               ← OPTIONAL advanced AI parser (off by default)
│   ├── organizer.py               ← folders, safe file names, financial year
│   ├── analytics.py               ← dashboard totals, search filters, CSV export
│   └── ui.py                      ← shared screens (invoice editor, tables, preview)
├── views\                         ← the 8 pages
│   ├── dashboard.py   upload.py   all_bills.py   suppliers.py
│   └── folders.py     search.py   details.py     settings.py
├── supabase\
│   └── schema.sql                 ← run once in Supabase (tables, security, bucket)
├── scripts\
│   ├── check_setup.py             ← checks your Supabase setup end-to-end
│   └── make_sample_invoices.py    ← re-creates the sample bills
├── sample_invoices\               ← 6 fictional bills for testing
└── tests\                         ← automated tests (41 tests)
```

Unzip the project to **`C:\Users\<YourName>\Documents\PurchaseDrive`**. All commands below are
typed in **PowerShell** inside that folder.

> To open PowerShell in the folder: open the folder in File Explorer, click the address bar,
> type `powershell` and press Enter.

---

# STAGE 1 — Cloud login, upload, storage, search, download

## Step 1 — Install Python 3.12

Open PowerShell (normal, not administrator) and run:

```powershell
winget install -e --id Python.Python.3.12
```

Close PowerShell and open it again, then check:

```powershell
py -3.12 --version
```

You should see `Python 3.12.x`.
*(No winget? Download "Python 3.12 Windows installer (64-bit)" from python.org and tick
**"Add python.exe to PATH"** during setup.)*

## Step 2 — Create the virtual environment and install the packages

In PowerShell, inside the `PurchaseDrive` folder:

```powershell
cd $HOME\Documents\PurchaseDrive
py -3.12 -m venv .venv
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

* When asked about the execution policy, type `Y` and Enter (this only allows local scripts).
* After activation your prompt starts with `(.venv)`. **Every time** you open a new PowerShell
  window for this project, run `cd $HOME\Documents\PurchaseDrive` and
  `.\.venv\Scripts\Activate.ps1` first.

## Step 3 — Create your Supabase project (the cloud)

1. Go to **https://supabase.com** → **Start your project** → sign up (GitHub or email).
2. **New project** → name `purchasedrive`, choose a strong **database password** (save it in a
   password manager), region **Mumbai (ap-south-1)** (closest to India) → **Create**.
   Wait ~2 minutes until the project is ready.
3. **Create the tables, security rules and private bucket:**
   left menu **SQL Editor** → **New query** → open `supabase\schema.sql` from the project in
   Notepad, select all, copy, paste into the editor → **Run**.
   You should see *"Success. No rows returned"*. (It is safe to run again later.)
4. **Check the bucket:** left menu **Storage** → you should see a bucket called **`invoices`**
   marked **Private**. Do **not** make it public.
5. **Email login settings:** left menu **Authentication**:
   * **Sign In / Providers → Email**: make sure Email is enabled. Keep **"Confirm email"** on
     (recommended).
   * **Emails → Templates → "Reset Password"**: so that "Forgot password" works inside the app,
     add this line to the message body and **Save**:
     ```html
     <p>Your PurchaseDrive reset code: <strong>{{ .Token }}</strong></p>
     ```

## Step 4 — Connect the app to Supabase (secrets.toml)

1. In Supabase: **Project Settings → API Keys** (or the green **Connect** button at the top).
   Copy:
   * the **Project URL** (`https://xxxxxxxx.supabase.co`)
   * the **Publishable key** (`sb_publishable_...`). On older projects you may instead see the
     legacy **anon public** key (`eyJ...`) — either one works.
   * ⚠️ **Never copy the `service_role` / `secret` key.** The app refuses to start with it.
2. In PowerShell:
   ```powershell
   copy .streamlit\secrets.toml.example .streamlit\secrets.toml
   notepad .streamlit\secrets.toml
   ```
3. Replace the two example values, save and close Notepad:
   ```toml
   SUPABASE_URL = "https://xxxxxxxx.supabase.co"
   SUPABASE_ANON_KEY = "sb_publishable_xxxxxxxxxxxxxxxx"
   ```

## Step 5 — Start the app and create your account

```powershell
streamlit run app.py
```

Your browser opens **http://localhost:8501**.

1. Tab **Create account** → your email + a strong password (min. 8 characters).
2. Open the confirmation email from Supabase and click the link (it may open a page that does not
   load — that is fine, the account is confirmed anyway).
3. Back in the app: **Log in**.
4. **Recommended for a private business drive:** once your account exists, switch off new
   sign-ups so nobody else can register: Supabase → **Authentication → Sign In / Providers** →
   turn off **"Allow new users to sign up"** → Save.

To stop the app: press `Ctrl + C` in PowerShell.

## Step 6 — Check the setup (optional but recommended)

Open a **second** PowerShell window in the project folder:

```powershell
.\.venv\Scripts\Activate.ps1
python scripts\check_setup.py
```

It logs in with your account and checks: tables exist, strangers cannot see data, upload /
download / delete in your private bucket work, and you cannot write into another user's folder.
Every line should say `[OK]`.

## Step 7 — Test Stage 1 (manual entry, no OCR yet)

1. **Settings & Account** → enter your business name. For testing with the sample bills enter
   **My business GSTIN** = `06AAACD1234E1ZD` (the made-up buyer printed on the samples). Later,
   replace it with your real GSTIN. → **Save settings**.
2. **Upload Purchase Bills** → drag `sample_invoices\01_ABC_Traders_INV-1023.pdf` into the box →
   **Upload to my cloud drive**.
   *This PDF contains real text, so its details are filled in even before Tesseract is installed.*
3. Check the fields → **Save & file this bill**. The message shows
   `PurchaseDrive/2026/October/ABC Traders/2026-10-05_INV-1023_ABC Traders.pdf`.
4. In Supabase → **Storage → invoices** you can see the file inside your user-id folder →
   `PurchaseDrive/2026/October/ABC Traders/`. **The original file is in the cloud.**
5. **Search Invoices** → Supplier name `abc` → 1 result. Tick the row → **Open** → **Download
   original**. The downloaded file is identical to the one you uploaded.

Stage 1 is complete when upload, search and download work.

---

# STAGE 2 — Automatic OCR and folder organisation

## Step 8 — Install Tesseract OCR

```powershell
winget install -e --id UB-Mannheim.TesseractOCR
```

*(No winget? Download the Windows installer from
https://github.com/UB-Mannheim/tesseract/wiki and keep the default folder
`C:\Program Files\Tesseract-OCR`.)*

Restart the app (`Ctrl + C`, then `streamlit run app.py`). The app finds Tesseract
automatically in `C:\Program Files\Tesseract-OCR\`. Check **Settings & Account → OCR engine** —
it should say *"Tesseract OCR 5.x found"*. If you installed it somewhere else, add the path to
`secrets.toml`:

```toml
TESSERACT_CMD = "D:\\Tools\\Tesseract-OCR\\tesseract.exe"
```

## Step 9 — Test OCR with the sample bills

Upload **all six files** from `sample_invoices\` at once (multi-select or drag them all).
Keep **"Read invoice details automatically (OCR)"** switched on.

| File | What it tests | Expected result |
|---|---|---|
| `01_ABC_Traders_INV-1023.pdf` | computer-made PDF, CGST+SGST, 3 HSN items | if you uploaded it in Stage 1: *"already uploaded"* warning → **Skip it** |
| `02_XYZ_Enterprises_photo.jpg` | tilted phone photo with shadow | XYZ Enterprises, GSTIN 06AAJFX5678L1ZB, XYZ/26-27/0457, 12-10-2026, ₹15,400.00, **Paid** |
| `03_Shree_Ganesh_Steel_IGST.png` | inter-state bill with IGST | Shree Ganesh Steel Pvt Ltd, IGST ₹8,514.00, total ₹55,814.00 |
| `04_ABC_Traders_scanned.pdf` | scanned PDF (image only) | INV-0988, 18-09-2026, ₹16,992.00, filed in **September** |
| `05_ABC_Traders_INV-1023_copy.jpg` | photo of the SAME bill as 01 | saved but marked **Duplicate** — dashboard total does not change |
| `06_Krishna_Hardware_partial.pdf` | no HSN codes, "Partially Paid" | 2 items, IGST ₹540.00, status **Partially paid** |

For each bill in **2. Check & save**: the preview is on the left, the fields on the right.
✅ fields were read clearly and pass all checks; ⚠️ fields need a look; ❓ fields were not found
and are left empty. Choose the payment status where needed (for 06, enter *Amount paid* 1000),
then **Save & file this bill**.

Afterwards:
* **Year & Month Folders** shows `2026 → November / October / September` with supplier
  sub-folders.
* **Supplier-wise Folders** shows ABC Traders with 2 bills (the duplicate is not counted).
* **Dashboard**: duplicates ignored = 1; balance due includes 01 + 03 + the rest of 06.

## Step 10 — Test search, downloads, edit and delete

* **Search Invoices**: type `0457`, `27AACCS`, `55814` or `photo` in *Quick search*; or use the
  filters: date range 01-10-2026 to 31-10-2026, financial year `2026-27`, payment status *Paid*.
* **Export CSV** on any list → opens in Excel.
* **Invoice Details**: change the supplier name or date → **Save changes** — the file moves to the
  new folder automatically. Choose **"Choose my own"** folder to file a bill anywhere
  (e.g. `Misc/Old bills`).
* **Delete**: Invoice Details → **Delete this bill** → you must type `DELETE` to confirm.
* **Mark not duplicate**: open the duplicate bill → *It's not a duplicate*.

## Step 11 — Automated tests (optional)

```powershell
pip install -r requirements-dev.txt
python -m pytest tests -v
```

The tests use a temporary in-memory store — they **never** touch your Supabase data. With
Tesseract installed all **41 tests** should pass (OCR tests are skipped if it is missing).

---

# STAGE 3 — Online access and iPhone

## Step 12 — Put the code on GitHub (private)

1. Create a free account at **https://github.com** and install **GitHub Desktop**
   (`winget install -e --id GitHub.GitHubDesktop`).
2. GitHub Desktop → **File → Add local repository** → choose the `PurchaseDrive` folder →
   *create a repository* → **Publish repository** → keep **"Keep this code private"** ticked.
3. Check on github.com that **`.streamlit/secrets.toml` is NOT in the repository**
   (`.gitignore` excludes it). If you ever see it there, delete the repo, create a new Supabase
   publishable key and publish again.

## Step 13 — Deploy on Streamlit Community Cloud (free)

1. Go to **https://share.streamlit.io** → sign in with GitHub → **Create app** → *Deploy a public
   app from GitHub*.
2. Repository: `your-name/PurchaseDrive`, branch `main`, main file `app.py`.
3. **Advanced settings**: Python version **3.12**; in **Secrets** paste the content of your
   local `secrets.toml` (the two lines `SUPABASE_URL = ...` and `SUPABASE_ANON_KEY = ...`).
4. **Deploy**. The first start takes a few minutes. `packages.txt` makes the cloud install
   Tesseract automatically.
5. Your app now has an address like `https://purchasedrive-yourname.streamlit.app`.
6. In Supabase → **Authentication → URL Configuration** → set **Site URL** to that address
   (so confirmation emails point to your app).

> The app's web address can be opened by anyone, but **nothing is visible without logging in**,
> and Supabase Row Level Security makes sure each account can only ever read its own rows and
> files — even if someone bypassed the app.

## Step 14 — Use it on your iPhone

1. Open **Safari** → type your app address (`https://....streamlit.app`) → log in.
2. Tap the **Share** button → **Add to Home Screen** → **Add**. You now have a PurchaseDrive icon
   like an app.
3. To add a bill: **Upload Purchase Bills** → tap the upload box → **Take Photo** (or Photo
   Library / Files) → **Upload to my cloud drive**.
   Tips for good OCR: flat bill, good light, no shadow of your phone, whole bill in the frame.
4. If your iPhone saves photos as HEIC and the app does not accept them: *Settings → Camera →
   Formats → Most Compatible* (photos taken via "Take Photo" in Safari are JPEG already).

**Quick test on the same Wi-Fi (before deploying):** when you run `streamlit run app.py`, it
prints a *Network URL* like `http://192.168.1.20:8501`. Open that on your iPhone while it is on the
same Wi-Fi (allow Python in the Windows Firewall prompt for *Private networks*). Use the online
address for normal use.

---

## Backup and restore

* **Settings & Account → Prepare backup ZIP → Download backup ZIP.** The ZIP contains every
  original bill in its folder structure, plus `data/invoices.csv`, `data/invoice_items.csv`
  (open in Excel) and `data/backup.json` (complete data for restoring).
  Make one every month and keep it on a USB drive or another cloud account.
* **Restore:** Settings → *Restore from a backup ZIP* → choose the ZIP → **Restore this backup**.
  Bills that are already in your drive are skipped, so restoring twice is safe. You can also
  restore into a brand-new Supabase project after running `schema.sql` there.
* Very large drives: the backup is built in memory; if you have several GB of bills, back up
  per financial year by exporting CSV and downloading files from Supabase Storage directly.

## Security checklist

- [x] Login required for every page (Supabase Auth, passwords never stored by the app).
- [x] Row Level Security on all three tables; storage policies limit each user to the folder
      named with their own user id. Verified by `scripts/check_setup.py`.
- [x] Bucket `invoices` is **private**; files are downloaded through the logged-in session only.
- [x] Only the publishable/anon key is used; the app stops if a service-role/secret key is
      configured. `secrets.toml` is git-ignored.
- [x] Uploads are checked: only real JPG/PNG/PDF files (content checked, not just the name),
      max 20 MB.
- [x] Nothing is overwritten: names get ` (2)`, ` (3)` …; duplicates are detected by file
      fingerprint (SHA-256) and by supplier + invoice number.
- [x] Deleting requires typing `DELETE`.
- [ ] Turn off "Allow new users to sign up" after creating your account (Step 5.4).
- [ ] Download a backup ZIP regularly.

## Good to know

* **Session:** for safety your login lives in the open browser tab. Reloading the page or
  reopening the tab later asks you to log in again.
* **Supabase free plan:** free projects are paused after a period without any activity (about a
  week at the time of writing). Your data is kept; open the Supabase dashboard and click
  **Restore project**. Paid plans are never paused. Check Supabase's pricing page for the
  current storage limits of the free plan.
* **Streamlit Community Cloud** puts apps to sleep when unused; the first visit wakes it up
  (about 30 seconds).
* **Optional AI parser (later):** for unusual bill layouts you can switch on a more advanced
  parser — see the instructions at the top of `core/ai_parser.py`. It only sends the bill's OCR
  text, and its answers pass the same checks before anything is marked as certain.

## Troubleshooting

| Problem | Fix |
|---|---|
| `py` is not recognised | Reinstall Python (Step 1), then close and reopen PowerShell. |
| `Activate.ps1 cannot be loaded` | Run `Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned`. |
| App says *Supabase is not configured* | `.streamlit\secrets.toml` missing or wrong names — redo Step 4. |
| *Database tables are missing* / *Bucket not found* | Run `supabase\schema.sql` again (Step 3.3). |
| *Wrong email or password* right after sign-up | Click the confirmation link in your email first. |
| Login error mentioning an invalid API key | Copy the key again; or use the legacy **anon** key from Settings → API Keys → Legacy tab. |
| *Permission denied by security rules* | You are using a different Supabase project than the one where you ran schema.sql. |
| OCR switch is grey | Tesseract not found — Step 8, then restart the app. |
| Photo fields mostly ❓ | Retake: flat, bright, sharp, whole bill visible; or type the values. |
| Forgot password email has no code | Add `{{ .Token }}` to the Reset Password template (Step 3.5). |
| `streamlit` is not recognised | Activate the virtual environment first: `.\.venv\Scripts\Activate.ps1`. |
