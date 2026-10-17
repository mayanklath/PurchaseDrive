"""Checks that your Supabase project is set up correctly (run AFTER Stage 1, step 4).

    python scripts/check_setup.py

It logs in with your PurchaseDrive account, checks the tables, uploads a tiny test
file to your private bucket, downloads it, deletes it again, and confirms that
someone who is NOT logged in cannot see your data.
"""
from __future__ import annotations

import getpass
import io
import os
import sys
import tomllib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core.backend import BackendError, SupabaseBackend  # noqa: E402
from core.config import BUCKET, looks_like_service_key  # noqa: E402


def ok(msg):
    print(f"  [OK]   {msg}")


def fail(msg):
    print(f"  [FAIL] {msg}")
    sys.exit(1)


def main():
    path = os.path.join(ROOT, ".streamlit", "secrets.toml")
    print("1. Reading .streamlit/secrets.toml")
    if not os.path.exists(path):
        fail("File not found. Copy secrets.toml.example to secrets.toml and fill it in.")
    with open(path, "rb") as f:
        secrets = tomllib.load(f)
    url = secrets.get("SUPABASE_URL")
    key = secrets.get("SUPABASE_ANON_KEY") or secrets.get("SUPABASE_PUBLISHABLE_KEY")
    if not url or not key or "YOUR-PROJECT" in url or key.startswith("PASTE-"):
        fail("SUPABASE_URL / SUPABASE_ANON_KEY are missing or still the example values.")
    if looks_like_service_key(key):
        fail("That is a SERVICE-ROLE/SECRET key. Use the anon / publishable key instead!")
    ok("URL and public key present (not a service key)")

    print("2. Checking that strangers see nothing (Row Level Security)")
    anon = SupabaseBackend(url, key)
    try:
        rows = anon.client.table("invoices").select("id").limit(1).execute().data
        if rows:
            fail("Rows are visible WITHOUT login - run supabase/schema.sql again!")
        ok("Not-logged-in visitors cannot read invoices")
    except Exception as exc:
        if "does not exist" in str(exc) or "PGRST205" in str(exc):
            fail("Table 'invoices' missing - run supabase/schema.sql in the SQL Editor.")
        ok("Not-logged-in visitors are refused")

    print("3. Logging in")
    email = input("   Your PurchaseDrive email: ").strip()
    pw = getpass.getpass("   Password (hidden): ")
    be = SupabaseBackend(url, key)
    try:
        user = be.sign_in(email, pw)
    except BackendError as exc:
        fail(f"Login failed: {exc}")
    ok(f"Logged in as {user.email}")

    print("4. Database tables")
    try:
        n = len(be.list_invoices())
        be.get_settings()
        ok(f"invoices / user_settings readable ({n} invoices)")
    except BackendError as exc:
        fail(str(exc))

    print(f"5. Private storage bucket '{BUCKET}'")
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (20, 20), "white").save(buf, "PNG")
    test_path = f"{user.id}/PurchaseDrive/_setup_check/test.png"
    try:
        be.delete_files([test_path])
        be.upload_file(test_path, buf.getvalue(), "image/png")
        ok("upload works")
        assert be.download_file(test_path) == buf.getvalue()
        ok("download works")
        be.delete_files([test_path])
        ok("delete works")
    except (BackendError, AssertionError) as exc:
        fail(f"Storage problem: {exc}")
    try:
        be.upload_file(f"someone-else/hack.png", buf.getvalue(), "image/png")
        be.delete_files(["someone-else/hack.png"])
        fail("Could write outside your own folder - run supabase/schema.sql again!")
    except BackendError:
        ok("Writing into another user's folder is blocked")

    print("6. Tesseract OCR (Stage 2)")
    from core.config import find_tesseract
    t = find_tesseract()
    print(f"  [{'OK' if t else '--'}]   " + (f"found at {t}" if t else
                                             "not installed yet (fine for Stage 1)"))
    be.sign_out()
    print("\nAll good! Start the app with:  streamlit run app.py")


if __name__ == "__main__":
    main()
