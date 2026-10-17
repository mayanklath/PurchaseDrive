"""Reads settings from .streamlit/secrets.toml (or environment variables).

Only the PUBLIC "anon" / "publishable" Supabase key is used by this app.
The service_role / secret key must NEVER be put in this project.
"""
from __future__ import annotations

import os
import platform
import shutil

APP_NAME = "PurchaseDrive AI"
BUCKET = "invoices"
ROOT_FOLDER = "PurchaseDrive"
INBOX_FOLDER = "Inbox"
MAX_FILE_MB = 20
ALLOWED_TYPES = ["jpg", "jpeg", "png", "pdf"]
MIME_BY_EXT = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "pdf": "application/pdf",
}


def get_setting(name: str, default: str | None = None) -> str | None:
    """Look up a setting in Streamlit secrets first, then environment variables."""
    try:
        import streamlit as st

        if name in st.secrets:
            return str(st.secrets[name])
    except Exception:
        pass
    return os.environ.get(name, default)


def supabase_credentials() -> tuple[str | None, str | None]:
    url = get_setting("SUPABASE_URL")
    key = get_setting("SUPABASE_ANON_KEY") or get_setting("SUPABASE_PUBLISHABLE_KEY")
    return url, key


def looks_like_service_key(key: str | None) -> bool:
    """Detect a service-role / secret key so the app can refuse to run with it."""
    if not key:
        return False
    if key.startswith("sb_secret_"):
        return True
    if key.count(".") == 2:  # legacy JWT key - inspect its role claim
        import base64
        import json

        try:
            payload = key.split(".")[1]
            payload += "=" * (-len(payload) % 4)
            data = json.loads(base64.urlsafe_b64decode(payload))
            return data.get("role") == "service_role"
        except Exception:
            return False
    return False


def find_tesseract() -> str | None:
    """Return the path to tesseract.exe / tesseract, or None if it is not installed."""
    configured = get_setting("TESSERACT_CMD")
    if configured and os.path.exists(configured):
        return configured
    found = shutil.which("tesseract")
    if found:
        return found
    if platform.system() == "Windows":
        for candidate in (
            r"C:\Program Files\Tesseract-OCR\tesseract.exe",
            r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
            os.path.expandvars(r"%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe"),
        ):
            if os.path.exists(candidate):
                return candidate
    return None
