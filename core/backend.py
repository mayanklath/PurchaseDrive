"""Talks to the cloud: Supabase Auth, the private Storage bucket and the Postgres tables.

`SupabaseBackend` is what the real app uses.
`MemoryBackend` keeps everything in RAM and exists ONLY for the automated tests in
`tests/` (it is switched on with the environment variable PD_TEST_BACKEND=memory).
"""
from __future__ import annotations

import copy
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from core.config import BUCKET

# Columns loaded for lists/search (the big OCR text is only loaded on the details page)
LIST_COLUMNS = (
    "id,created_at,updated_at,original_file_name,file_name,storage_path,folder_path,"
    "mime_type,file_size,file_sha256,supplier_name,supplier_gstin,invoice_number,"
    "invoice_date,financial_year,taxable_amount,cgst_amount,sgst_amount,igst_amount,"
    "total_amount,amount_paid,payment_status,review_status,is_duplicate,duplicate_of,"
    "ocr_confidence,notes"
)
ITEM_FIELDS = ["line_no", "description", "hsn_code", "quantity", "unit", "rate",
               "taxable_amount", "gst_rate"]


class BackendError(Exception):
    """A friendly error message that can be shown to the user."""


@dataclass
class User:
    id: str
    email: str


def _friendly(exc: Exception) -> str:
    msg = getattr(exc, "message", None) or str(exc)
    low = msg.lower()
    if "invalid login credentials" in low:
        return "Wrong email or password."
    if "email not confirmed" in low:
        return "Please confirm your email first (check your inbox), then log in."
    if "row-level security" in low or "violates row-level" in low:
        return "Permission denied by security rules. Did you run supabase/schema.sql?"
    if "bucket not found" in low:
        return "Storage bucket 'invoices' not found. Run supabase/schema.sql in Supabase."
    if "duplicate" in low and "exists" in low:
        return "A file with this name already exists in that folder."
    if "jwt expired" in low:
        return "Your login expired. Please log in again."
    if "relation" in low and "does not exist" in low:
        return "Database tables are missing. Run supabase/schema.sql in the Supabase SQL Editor."
    return msg


# =====================================================================
#  Real backend: Supabase
# =====================================================================
class SupabaseBackend:
    """One instance per browser session (never shared between users)."""

    def __init__(self, url: str, key: str):
        from supabase import create_client
        from supabase.lib.client_options import SyncClientOptions

        self.client = create_client(
            url, key, options=SyncClientOptions(auto_refresh_token=True, persist_session=True)
        )

    # ---------------- auth ----------------
    def sign_in(self, email: str, password: str) -> User:
        try:
            res = self.client.auth.sign_in_with_password({"email": email, "password": password})
        except Exception as exc:
            raise BackendError(_friendly(exc)) from exc
        return User(id=res.user.id, email=res.user.email or email)

    def sign_up(self, email: str, password: str) -> bool:
        """Create an account. Returns True if the user must confirm their email first."""
        try:
            res = self.client.auth.sign_up({"email": email, "password": password})
        except Exception as exc:
            raise BackendError(_friendly(exc)) from exc
        return res.session is None

    def send_reset_code(self, email: str) -> None:
        try:
            self.client.auth.reset_password_for_email(email)
        except Exception as exc:
            raise BackendError(_friendly(exc)) from exc

    def reset_password_with_code(self, email: str, code: str, new_password: str) -> User:
        try:
            res = self.client.auth.verify_otp({"email": email, "token": code.strip(),
                                               "type": "recovery"})
            self.client.auth.update_user({"password": new_password})
        except Exception as exc:
            raise BackendError(_friendly(exc)) from exc
        return User(id=res.user.id, email=res.user.email or email)

    def change_password(self, new_password: str) -> None:
        try:
            self.client.auth.update_user({"password": new_password})
        except Exception as exc:
            raise BackendError(_friendly(exc)) from exc

    def sign_out(self) -> None:
        try:
            self.client.auth.sign_out()
        except Exception:
            pass

    def current_user(self) -> User | None:
        try:
            session = self.client.auth.get_session()  # refreshes an expired token
        except Exception:
            return None
        if not session or not session.user:
            return None
        return User(id=session.user.id, email=session.user.email or "")

    # ---------------- storage ----------------
    def _bucket(self):
        return self.client.storage.from_(BUCKET)

    def upload_file(self, path: str, data: bytes, mime: str) -> None:
        try:
            self._bucket().upload(path, data, {"content-type": mime, "upsert": "false"})
        except Exception as exc:
            raise BackendError(_friendly(exc)) from exc

    def download_file(self, path: str) -> bytes:
        try:
            return self._bucket().download(path)
        except Exception as exc:
            raise BackendError(_friendly(exc)) from exc

    def move_file(self, src: str, dst: str) -> None:
        try:
            self._bucket().move(src, dst)
        except Exception as exc:
            raise BackendError(_friendly(exc)) from exc

    def delete_files(self, paths: list[str]) -> None:
        if not paths:
            return
        try:
            self._bucket().remove(paths)
        except Exception as exc:
            raise BackendError(_friendly(exc)) from exc

    def list_folder(self, folder: str) -> list[str]:
        """File names directly inside a storage folder."""
        try:
            items = self._bucket().list(folder, {"limit": 1000, "offset": 0})
        except Exception:
            return []
        return [i["name"] for i in items if i.get("id")]  # folders have no id

    # ---------------- invoices ----------------
    def list_invoices(self, full: bool = False) -> list[dict]:
        """All of the user's invoices (RLS guarantees only their own rows come back)."""
        rows: list[dict] = []
        start, page = 0, 1000
        try:
            while True:
                res = (self.client.table("invoices").select("*" if full else LIST_COLUMNS)
                       .order("created_at", desc=True).range(start, start + page - 1).execute())
                rows.extend(res.data or [])
                if len(res.data or []) < page:
                    break
                start += page
        except Exception as exc:
            raise BackendError(_friendly(exc)) from exc
        return rows

    def get_invoice(self, invoice_id: str) -> dict | None:
        try:
            res = self.client.table("invoices").select("*").eq("id", invoice_id).execute()
        except Exception as exc:
            raise BackendError(_friendly(exc)) from exc
        return res.data[0] if res.data else None

    def find_by_sha(self, sha: str) -> list[dict]:
        try:
            res = (self.client.table("invoices").select(LIST_COLUMNS)
                   .eq("file_sha256", sha).execute())
        except Exception as exc:
            raise BackendError(_friendly(exc)) from exc
        return res.data or []

    def insert_invoice(self, row: dict) -> dict:
        try:
            res = self.client.table("invoices").insert(row).execute()
        except Exception as exc:
            raise BackendError(_friendly(exc)) from exc
        return res.data[0]

    def update_invoice(self, invoice_id: str, changes: dict) -> dict:
        try:
            res = self.client.table("invoices").update(changes).eq("id", invoice_id).execute()
        except Exception as exc:
            raise BackendError(_friendly(exc)) from exc
        if not res.data:
            raise BackendError("Invoice not found (or you do not have access to it).")
        return res.data[0]

    def delete_invoice(self, invoice_id: str) -> None:
        try:
            self.client.table("invoices").delete().eq("id", invoice_id).execute()
        except Exception as exc:
            raise BackendError(_friendly(exc)) from exc

    # ---------------- line items ----------------
    def get_items(self, invoice_id: str) -> list[dict]:
        try:
            res = (self.client.table("invoice_items").select("*").eq("invoice_id", invoice_id)
                   .order("line_no").execute())
        except Exception as exc:
            raise BackendError(_friendly(exc)) from exc
        return res.data or []

    def list_all_items(self) -> list[dict]:
        rows: list[dict] = []
        start, page = 0, 1000
        try:
            while True:
                res = (self.client.table("invoice_items").select("*").order("invoice_id")
                       .order("line_no").range(start, start + page - 1).execute())
                rows.extend(res.data or [])
                if len(res.data or []) < page:
                    break
                start += page
        except Exception as exc:
            raise BackendError(_friendly(exc)) from exc
        return rows

    def replace_items(self, invoice_id: str, items: list[dict]) -> None:
        try:
            self.client.table("invoice_items").delete().eq("invoice_id", invoice_id).execute()
            if items:
                payload = [{**{k: it.get(k) for k in ITEM_FIELDS}, "invoice_id": invoice_id}
                           for it in items]
                self.client.table("invoice_items").insert(payload).execute()
        except Exception as exc:
            raise BackendError(_friendly(exc)) from exc

    # ---------------- settings ----------------
    def get_settings(self) -> dict:
        try:
            res = self.client.table("user_settings").select("*").execute()
        except Exception as exc:
            raise BackendError(_friendly(exc)) from exc
        return res.data[0] if res.data else {}

    def save_settings(self, user_id: str, settings: dict) -> None:
        try:
            self.client.table("user_settings").upsert(
                {**settings, "user_id": user_id}, on_conflict="user_id").execute()
        except Exception as exc:
            raise BackendError(_friendly(exc)) from exc


# =====================================================================
#  Test backend: in memory (used by tests/ only)
# =====================================================================
_MEMORY: dict[str, Any] = {
    "users": {},       # email -> {"id","password"}
    "files": {},       # path -> bytes
    "invoices": {},    # id -> row
    "items": {},       # id -> row
    "settings": {},    # user_id -> row
}


def reset_memory_store() -> None:
    for v in _MEMORY.values():
        v.clear()


class MemoryBackend:
    def __init__(self, *_args, **_kwargs):
        self._user: User | None = None

    # auth
    def sign_up(self, email: str, password: str) -> bool:
        if email in _MEMORY["users"]:
            raise BackendError("User already registered")
        _MEMORY["users"][email] = {"id": str(uuid.uuid4()), "password": password}
        return False

    def sign_in(self, email: str, password: str) -> User:
        u = _MEMORY["users"].get(email)
        if not u or u["password"] != password:
            raise BackendError("Wrong email or password.")
        self._user = User(u["id"], email)
        return self._user

    def send_reset_code(self, email: str) -> None:
        return None

    def reset_password_with_code(self, email, code, new_password) -> User:
        if code != "123456" or email not in _MEMORY["users"]:
            raise BackendError("Token has expired or is invalid")
        _MEMORY["users"][email]["password"] = new_password
        return self.sign_in(email, new_password)

    def change_password(self, new_password: str) -> None:
        _MEMORY["users"][self._user.email]["password"] = new_password

    def sign_out(self) -> None:
        self._user = None

    def current_user(self) -> User | None:
        return self._user

    def _uid(self) -> str:
        if not self._user:
            raise BackendError("Not logged in")
        return self._user.id

    def _check_path(self, path: str) -> None:
        if path.split("/")[0] != self._uid():
            raise BackendError("new row violates row-level security policy")

    # storage
    def upload_file(self, path, data, mime):
        self._check_path(path)
        if path in _MEMORY["files"]:
            raise BackendError("The resource already exists")
        _MEMORY["files"][path] = bytes(data)

    def download_file(self, path):
        self._check_path(path)
        if path not in _MEMORY["files"]:
            raise BackendError("Object not found")
        return _MEMORY["files"][path]

    def move_file(self, src, dst):
        self._check_path(src)
        self._check_path(dst)
        if dst in _MEMORY["files"]:
            raise BackendError("The resource already exists")
        _MEMORY["files"][dst] = _MEMORY["files"].pop(src)

    def delete_files(self, paths):
        for p in paths:
            self._check_path(p)
            _MEMORY["files"].pop(p, None)

    def list_folder(self, folder):
        folder = folder.rstrip("/") + "/"
        return [p[len(folder):] for p in _MEMORY["files"]
                if p.startswith(folder) and "/" not in p[len(folder):]]

    # invoices
    def _mine(self):
        uid = self._uid()
        return [r for r in _MEMORY["invoices"].values() if r["user_id"] == uid]

    def list_invoices(self, full=False):
        rows = [{k: v for k, v in r.items() if full or k not in ("ocr_text", "extraction")}
                for r in self._mine()]
        return copy.deepcopy(sorted(rows, key=lambda r: r["created_at"], reverse=True))

    def get_invoice(self, invoice_id):
        r = _MEMORY["invoices"].get(invoice_id)
        return copy.deepcopy(r) if r and r["user_id"] == self._uid() else None

    def find_by_sha(self, sha):
        return copy.deepcopy([r for r in self._mine() if r.get("file_sha256") == sha])

    def insert_invoice(self, row):
        now = datetime.now(timezone.utc).isoformat()
        rec = {"id": str(uuid.uuid4()), "user_id": self._uid(), "created_at": now,
               "updated_at": now, "folder_path": "Inbox", "payment_status": "unknown",
               "review_status": "pending", "is_duplicate": False, "duplicate_of": None,
               "extraction": {}, "notes": None, "ocr_text": None, "ocr_confidence": None}
        for col in ("supplier_name", "supplier_gstin", "invoice_number", "invoice_date",
                    "financial_year", "taxable_amount", "cgst_amount", "sgst_amount",
                    "igst_amount", "total_amount", "amount_paid", "mime_type", "file_size",
                    "file_sha256"):
            rec[col] = None
        rec.update(copy.deepcopy(row))
        if any(r["storage_path"] == rec["storage_path"] for r in self._mine()):
            raise BackendError("duplicate key value violates unique constraint")
        _MEMORY["invoices"][rec["id"]] = rec
        return copy.deepcopy(rec)

    def update_invoice(self, invoice_id, changes):
        r = _MEMORY["invoices"].get(invoice_id)
        if not r or r["user_id"] != self._uid():
            raise BackendError("Invoice not found (or you do not have access to it).")
        r.update(copy.deepcopy(changes))
        r["updated_at"] = datetime.now(timezone.utc).isoformat()
        return copy.deepcopy(r)

    def delete_invoice(self, invoice_id):
        r = _MEMORY["invoices"].get(invoice_id)
        if r and r["user_id"] == self._uid():
            del _MEMORY["invoices"][invoice_id]
            for k in [k for k, it in _MEMORY["items"].items() if it["invoice_id"] == invoice_id]:
                del _MEMORY["items"][k]
            for other in _MEMORY["invoices"].values():
                if other.get("duplicate_of") == invoice_id:
                    other["duplicate_of"] = None

    def get_items(self, invoice_id):
        uid = self._uid()
        items = [it for it in _MEMORY["items"].values()
                 if it["invoice_id"] == invoice_id and it["user_id"] == uid]
        return copy.deepcopy(sorted(items, key=lambda i: i["line_no"]))

    def list_all_items(self):
        uid = self._uid()
        return copy.deepcopy([it for it in _MEMORY["items"].values() if it["user_id"] == uid])

    def replace_items(self, invoice_id, items):
        uid = self._uid()
        for k in [k for k, it in _MEMORY["items"].items() if it["invoice_id"] == invoice_id]:
            del _MEMORY["items"][k]
        for it in items:
            iid = str(uuid.uuid4())
            _MEMORY["items"][iid] = {**{k: it.get(k) for k in ITEM_FIELDS}, "id": iid,
                                     "invoice_id": invoice_id, "user_id": uid}

    def get_settings(self):
        return copy.deepcopy(_MEMORY["settings"].get(self._uid(), {}))

    def save_settings(self, user_id, settings):
        _MEMORY["settings"][self._uid()] = {**settings, "user_id": self._uid()}
