"""OPTIONAL advanced parser: asks Claude (Anthropic API) to read the OCR text.

It is OFF by default. To switch it on later add these two lines to
.streamlit/secrets.toml (or to the Streamlit Cloud "Secrets" box):

    ANTHROPIC_API_KEY = "sk-ant-..."
    AI_PARSER = "claude"

Notes
* Only the OCR TEXT of the bill is sent (not the image), over HTTPS.
* The AI's answer goes through the same safety checks as the built-in parser
  (GSTIN check digit, taxable + GST = total). Nothing is marked "high" unless the
  checks pass, so you still review anything uncertain before saving.
* If the API call fails for any reason, the app silently uses the built-in parser.
"""
from __future__ import annotations

import json
import re
from datetime import date

import requests

from core.config import get_setting
from core.extractor import (AMOUNT_FIELDS, Extraction, FieldResult, _cross_check,
                            gstin_checksum_ok, parse_amount)

API_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = "claude-sonnet-5-5"

PROMPT = """You extract data from the OCR text of an Indian GST purchase invoice.
Return ONLY a JSON object with these keys (use null when a value is not clearly printed;
NEVER guess or calculate a value that is not printed):
supplier_name, supplier_gstin, invoice_number, invoice_date (YYYY-MM-DD; Indian bills use
DD/MM/YYYY), taxable_amount, cgst_amount, sgst_amount, igst_amount, total_amount,
payment_status (one of "paid","unpaid","partially_paid" or null),
items (list of {description, hsn_code, quantity, unit, rate, taxable_amount, gst_rate}).
The supplier is the SELLER, never the "Bill to"/buyer party.%s
OCR TEXT:
<<<
%s
>>>"""


def ai_enabled() -> bool:
    return (get_setting("AI_PARSER", "") or "").lower() == "claude" and \
        bool(get_setting("ANTHROPIC_API_KEY"))


def parse_with_claude(text: str, my_gstin: str | None = None,
                      my_business_name: str | None = None) -> Extraction:
    key = get_setting("ANTHROPIC_API_KEY")
    model = get_setting("AI_MODEL", DEFAULT_MODEL)
    hint = ""
    if my_gstin or my_business_name:
        hint = (f"\nThe BUYER (ignore as supplier) is: {my_business_name or ''} "
                f"GSTIN {my_gstin or ''}.")
    resp = requests.post(
        API_URL, timeout=60,
        headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
        json={"model": model, "max_tokens": 2000,
              "messages": [{"role": "user", "content": PROMPT % (hint, text[:15000])}]},
    )
    resp.raise_for_status()
    out = "".join(b.get("text", "") for b in resp.json().get("content", []))
    m = re.search(r"\{.*\}", out, re.S)
    if not m:
        raise ValueError("AI parser returned no JSON")
    data = json.loads(m.group(0))
    return _to_extraction(data, text)


def _to_extraction(data: dict, text: str) -> Extraction:
    ex = Extraction(parser="claude")
    compact = re.sub(r"\s+", "", text).upper()

    def put(name, value, conf="medium"):
        if value not in (None, "", []):
            ex.fields[name] = FieldResult(value, conf, "AI parser")

    put("supplier_name", (data.get("supplier_name") or "").strip() or None)
    g = (data.get("supplier_gstin") or "").replace(" ", "").upper()
    if g:
        put("supplier_gstin", g, "high" if gstin_checksum_ok(g) and g in compact else "low")
    inv = (data.get("invoice_number") or "").strip()
    if inv:
        put("invoice_number", inv, "high" if re.sub(r"\s", "", inv).upper() in compact else "low")
    try:
        d = date.fromisoformat(str(data.get("invoice_date"))) if data.get("invoice_date") else None
    except ValueError:
        d = None
    put("invoice_date", d)
    for k in AMOUNT_FIELDS:
        put(k, parse_amount(data.get(k)))
    if data.get("payment_status") in ("paid", "unpaid", "partially_paid"):
        put("payment_status", data["payment_status"])
    for n, it in enumerate(data.get("items") or [], 1):
        ex.items.append({
            "line_no": n, "description": it.get("description"), "hsn_code": it.get("hsn_code"),
            "quantity": parse_amount(it.get("quantity")), "unit": it.get("unit"),
            "rate": parse_amount(it.get("rate")),
            "taxable_amount": parse_amount(it.get("taxable_amount")),
            "gst_rate": parse_amount(it.get("gst_rate")), "confidence": "medium"})
    _cross_check(ex)
    ex.notes.insert(0, "Read by the AI parser - please check highlighted fields.")
    return ex
