"""Reads invoice fields out of OCR text (Indian GST purchase bills).

Golden rule: NEVER invent a value. If something is not clearly printed on the bill
the field stays empty. Every field gets a confidence:
    high    -> found with a clear label and passed cross-checks
    medium  -> probably right, please glance at it
    low     -> a guess, please check
    missing -> not found
Anything that is not "high" is highlighted for manual correction before saving.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

FIELDS = ["supplier_name", "supplier_gstin", "invoice_number", "invoice_date",
          "taxable_amount", "cgst_amount", "sgst_amount", "igst_amount", "total_amount",
          "payment_status"]
AMOUNT_FIELDS = ["taxable_amount", "cgst_amount", "sgst_amount", "igst_amount", "total_amount"]

MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
MONTHS["sept"] = 9


@dataclass
class FieldResult:
    value: object = None
    confidence: str = "missing"
    source: str = ""

    def as_dict(self):
        v = self.value.isoformat() if isinstance(self.value, date) else self.value
        return {"value": v, "confidence": self.confidence, "source": self.source[:160]}


@dataclass
class Extraction:
    fields: dict[str, FieldResult] = field(default_factory=lambda: {f: FieldResult() for f in FIELDS})
    items: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    parser: str = "rules"

    def value(self, name):
        return self.fields[name].value

    def not_applicable(self, name: str) -> bool:
        """Fields that are normally absent: CGST/SGST on an IGST bill (and the reverse),
        and payment status (usually chosen by you)."""
        f = self.fields
        if name in ("cgst_amount", "sgst_amount"):
            return f[name].value is None and f["igst_amount"].value is not None
        if name == "igst_amount":
            return f[name].value is None and (f["cgst_amount"].value is not None
                                              or f["sgst_amount"].value is not None)
        if name == "payment_status":
            return f[name].value is None
        return False

    def uncertain_fields(self) -> list[str]:
        return [n for n, r in self.fields.items()
                if r.confidence != "high" and not self.not_applicable(n)]

    def as_dict(self) -> dict:
        return {"fields": {k: v.as_dict() for k, v in self.fields.items()},
                "items": self.items, "notes": self.notes, "parser": self.parser}


# =====================================================================
#  GSTIN
# =====================================================================
_GST_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
GSTIN_RE = re.compile(r"^\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")


def gstin_checksum_ok(g: str) -> bool:
    g = g.upper()
    if not GSTIN_RE.match(g):
        return False
    total = 0
    for i, ch in enumerate(g[:14]):
        p = _GST_CHARS.index(ch) * (2 if i % 2 else 1)
        total += p // 36 + p % 36
    return _GST_CHARS[(36 - total % 36) % 36] == g[14]


_TO_DIGIT = str.maketrans({"O": "0", "D": "0", "Q": "0", "I": "1", "L": "1", "|": "1",
                           "Z": "2", "S": "5", "B": "8", "G": "6", "T": "7"})
_TO_ALPHA = str.maketrans({"0": "O", "1": "I", "2": "Z", "5": "S", "8": "B", "6": "G", "4": "A"})
_LOOKALIKE = {"0": "OD", "O": "0DQ", "D": "0OP", "P": "D", "1": "I7L", "I": "1L", "L": "1I",
              "7": "1T", "2": "Z", "Z": "27", "5": "S", "S": "5", "8": "B", "B": "8",
              "6": "G", "G": "6C", "C": "G", "4": "A", "A": "4", "E": "F", "F": "E"}


def _format_fix(g: str) -> str | None:
    if len(g) != 15:
        return None
    fixed = (g[0:2].translate(_TO_DIGIT) + g[2:7].translate(_TO_ALPHA)
             + g[7:11].translate(_TO_DIGIT) + g[11].translate(_TO_ALPHA)
             + g[12] + "Z" + g[14])
    return fixed if GSTIN_RE.match(fixed) else None


def repair_gstin(raw: str) -> tuple[str, bool] | None:
    """Fix typical OCR mistakes (O<->0, extra letter, Z read as 2 ...).

    Returns (gstin, checksum_ok) or None. Only a correction that makes the GSTIN's
    own check digit valid is trusted; otherwise the best-looking text is returned
    with checksum_ok=False so the app asks you to check it.
    """
    g = re.sub(r"[^0-9A-Za-z|]", "", raw).upper()
    variants = [g] if len(g) == 15 else (
        [g[:i] + g[i + 1:] for i in range(len(g))] if len(g) == 16 else [])
    fallback = None
    for v in variants:
        f = _format_fix(v)
        if not f:
            continue
        if gstin_checksum_ok(f):
            return f, True
        fallback = fallback or f
    if fallback is None:
        return None
    # one look-alike character wrong? accept only if exactly one fix is valid
    valid = set()
    for i, ch in enumerate(fallback):
        for alt in _LOOKALIKE.get(ch, ""):
            cand = fallback[:i] + alt + fallback[i + 1:]
            if gstin_checksum_ok(cand):
                valid.add(cand)
    if len(valid) == 1:
        return valid.pop(), True
    return fallback, False


_GSTIN_LABELLED = re.compile(r"GST\s*I?\s*N\s*(?:No\.?|Number)?\s*[:\-.]?\s*([0-9A-Za-z|][0-9A-Za-z| ]{13,19})",
                             re.I)
_GSTIN_CANDIDATE = re.compile(r"(?<![0-9A-Za-z])([0-9OIlSBZDQ|]{2}[A-Za-z0-9]{5}[0-9OIlSBZDQGT|]{4}"
                              r"[A-Za-z0-9]{4})(?![0-9A-Za-z])")


def find_gstins(line: str) -> list[tuple[str, bool]]:
    """Return [(gstin, checksum_ok)] found in a line."""
    raws = []
    for m in _GSTIN_LABELLED.finditer(line):
        tok = m.group(1).split()
        # join pieces split by stray spaces until we have 15-16 characters
        joined = ""
        for piece in tok:
            if len(joined) >= 15:
                break
            joined += piece
        raws.append(joined[:16])
    raws += [m.group(1) for m in _GSTIN_CANDIDATE.finditer(line)]
    out: list[tuple[str, bool]] = []
    for raw in raws:
        r = repair_gstin(raw)
        if r and r[0] not in [o[0] for o in out]:
            out.append(r)
    return out


def _near(a: str, b: str | None, max_diff: int = 2) -> bool:
    return bool(b) and len(a) == len(b) and sum(x != y for x, y in zip(a, b)) <= max_diff


# =====================================================================
#  Numbers and dates
# =====================================================================
_NUM_RE = re.compile(r"(?<![\w.%/-])(?:₹|rs\.?|inr)?\s*(-?\d{1,3}(?:,\d{2,3})+(?:\.\d{1,2})?|-?\d+(?:\.\d{1,2})?)"
                     r"(?!\s*%)(?![\w%/]|\.\d)", re.I)


def parse_amount(s) -> float | None:
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return round(float(s), 2)
    s = str(s).replace("₹", "").replace(",", "").replace("Rs.", "").replace("Rs", "").strip()
    try:
        return round(float(s), 2)
    except ValueError:
        return None


def amounts_in(line: str) -> list[float]:
    """All money-like numbers on a line (percentages are skipped)."""
    vals = []
    for m in _NUM_RE.finditer(line):
        v = parse_amount(m.group(1))
        if v is not None:
            vals.append(v)
    return vals


_DATE_PATTERNS = [
    re.compile(r"(?<!\d)(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?!\d)"),                       # 2026-10-05
    re.compile(r"(?<!\d)(\d{1,2})\s*[-/.]\s*(\d{1,2})\s*[-/.]\s*(\d{4}|\d{2})(?!\d)"),      # 05/10/2026
    re.compile(r"(?<!\d)(\d{1,2})(?:st|nd|rd|th)?[\s\-/.,]*([A-Za-z]{3,9})[\s\-/.,']*(\d{4}|\d{2})(?!\d)"),  # 5 Oct 2026
    re.compile(r"\b([A-Za-z]{3,9})[\s\-/.]*(\d{1,2})(?:st|nd|rd|th)?,?[\s\-/.]*(\d{4})(?!\d)"),  # Oct 5, 2026
]


def _mk_date(y: int, m: int, d: int) -> date | None:
    if y < 100:
        y += 2000
    if not (2000 <= y <= date.today().year + 1):
        return None
    try:
        return date(y, m, d)
    except ValueError:
        return None


def find_dates(line: str) -> list[tuple[date, bool]]:
    """Return [(date, ambiguous)] in the order they appear. Indian dd/mm/yyyy is assumed."""
    found: list[tuple[int, date, bool]] = []
    for idx, pat in enumerate(_DATE_PATTERNS):
        for m in pat.finditer(line):
            a, b, c = m.groups()
            d, ambiguous = None, False
            if idx == 0:
                d = _mk_date(int(a), int(b), int(c))
            elif idx == 1:
                dd, mm = int(a), int(b)
                if mm > 12 and dd <= 12:          # clearly mm/dd
                    dd, mm, ambiguous = mm, dd, True
                d = _mk_date(int(c), mm, dd)
            elif idx == 2:
                mon = MONTHS.get(b[:4].lower()) or MONTHS.get(b[:3].lower())
                if mon:
                    d = _mk_date(int(c), mon, int(a))
            else:
                mon = MONTHS.get(a[:4].lower()) or MONTHS.get(a[:3].lower())
                if mon:
                    d = _mk_date(int(c), mon, int(b))
            if d:
                found.append((m.start(), d, ambiguous))
    found.sort(key=lambda t: t[0])
    out, seen = [], set()
    for _, d, amb in found:
        if d not in seen:
            seen.add(d)
            out.append((d, amb))
    return out


# =====================================================================
#  Main parser
# =====================================================================
_BUYER_RE = re.compile(r"\b(bill(?:ed)?\s*to|buyer|ship(?:ped)?\s*to|consignee|recipient|"
                       r"customer|sold\s*to|details\s*of\s*receiver|party\s*details|to\s*:)", re.I)
_SUPPLIER_LABEL_RE = re.compile(r"^\s*(?:supplier|seller|vendor|sold\s*by|from|"
                                r"details\s*of\s*supplier)\s*(?:name)?\s*[:\-]\s*(.+)$", re.I)
_COMPANY_RE = re.compile(r"\b(traders?|trading|enterprises?|pvt\.?|private|ltd\.?|limited|llp|"
                         r"industries|agenc(?:y|ies)|stores?|corporation|corp\.?|company|& ?sons|"
                         r"suppliers?|distributors?|mart|brothers|bros\.?|hardware|steels?|"
                         r"electricals?|chemicals?|textiles?|pharma|foods?|exports?|imports?|"
                         r"associates|udyog|works|mills?|solutions|wholesale)\b", re.I)
_HEADER_WORDS = re.compile(r"(tax\s*invoice|invoice|original|duplicate|triplicate|recipient|"
                           r"transporter|gstin|phone|ph\.|mob|email|e-mail|www\.|state|address|"
                           r"pin|cin|pan|bill of supply|cash memo|estimate|page)", re.I)
_TABLE_HEAD_RE = re.compile(r"\b(hsn|sac|description|particulars|item\s*name|product)\b", re.I)
_TOTALS_START_RE = re.compile(r"\b(sub\s*total|taxable\s*(?:value|amount|amt)|total|cgst|sgst|igst|"
                              r"round\s*off)\b", re.I)
_UNIT_RE = re.compile(r"^(nos?|pcs?|pieces?|kgs?|kg|g|gm|gms|ltrs?|l|ltr|mtrs?|m|bags?|box(?:es)?|"
                      r"sets?|pkts?|packs?|dozen|dz|units?|rolls?|sheets?|pairs?|cartons?|ctn|"
                      r"btl|bottles?|tins?|qtl|mt|tonnes?|sqft|sqm|feet|ft)$", re.I)

_INV_NO_RE = re.compile(
    r"\b(?:tax\s*)?(?:invoice|inv|bill|voucher)\s*(?:no|number|num|#)\b\.?\s*[:\-#.]?\s*"
    r"([A-Za-z0-9][A-Za-z0-9\-/_.]{0,24})", re.I)
_INV_NO_LABEL_ONLY = re.compile(r"\b(?:invoice|inv|bill)\s*(?:no|number|#)\.?\s*[:\-]?\s*$", re.I)
_INV_DATE_LABEL = re.compile(r"\b(?:invoice|inv|bill|tax\s*invoice)\s*date\b|\bdated\b", re.I)
_PLAIN_DATE_LABEL = re.compile(r"(?<![a-z])date\s*[:\-.]", re.I)
_OTHER_DATE_LABEL = re.compile(r"\b(due|ack|e-?way|challan|order|p\.?o\.?|delivery|dispatch|"
                               r"lr|supply|reverse|payment)\s*(?:no\.?\s*&\s*)?date\b", re.I)

_TOTAL_KEYS = [  # (regex, priority) - higher priority wins
    (re.compile(r"\b(grand\s*total|invoice\s*total|total\s*invoice\s*(?:value|amount)|"
                r"net\s*payable|amount\s*payable|total\s*payable|invoice\s*value|bill\s*amount|"
                r"net\s*amount)\b", re.I), 3),
    (re.compile(r"\btotal\s*amount\b", re.I), 2),
    (re.compile(r"^\s*total\b", re.I), 1),
]
_NOT_TOTAL = re.compile(r"(total\s*tax|taxable|sub\s*total|total\s*q(?:uan)?ty|total\s*gst|"
                        r"in\s*words|total\s*items|total\s*cgst|total\s*sgst|total\s*igst|"
                        r"total\s*discount|before\s*tax)", re.I)
_KEYS = {
    "taxable_amount": re.compile(r"\b(taxable\s*(?:value|amount|amt)|sub\s*-?\s*total|"
                                 r"assessable\s*value|(?:amount|total)\s*before\s*tax)\b", re.I),
    "cgst_amount": re.compile(r"\b(cgst|central\s*tax)\b", re.I),
    "sgst_amount": re.compile(r"\b(sgst|utgst|state\s*tax)\b", re.I),
    "igst_amount": re.compile(r"\b(igst|integrated\s*tax)\b", re.I),
}
_ROUND_RE = re.compile(r"\bround(?:ed)?\s*-?\s*off|rounding\b", re.I)


def _clean_lines(text: str) -> list[str]:
    lines = []
    for raw in text.replace("\r", "\n").split("\n"):
        line = re.sub(r"[ \t]+", " ", raw).strip(" |:;")
        if line:
            lines.append(line)
    return lines


def parse_invoice_text(text: str, my_gstin: str | None = None,
                       my_business_name: str | None = None) -> Extraction:
    ex = Extraction()
    lines = _clean_lines(text or "")
    if not lines:
        ex.notes.append("No text could be read from this file.")
        return ex
    my_gstin = (my_gstin or "").strip().upper() or None
    my_name = (my_business_name or "").strip().lower() or None

    buyer_idx = next((i for i, l in enumerate(lines) if _BUYER_RE.search(l)), None)
    table_idx = next((i for i, l in enumerate(lines) if _TABLE_HEAD_RE.search(l)
                      and i > 1), None)

    _extract_gstin(ex, lines, buyer_idx, table_idx, my_gstin)
    _extract_supplier(ex, lines, buyer_idx, my_gstin, my_name)
    _extract_invoice_number(ex, lines)
    _extract_date(ex, lines)
    _extract_amounts(ex, lines, table_idx)
    _extract_payment(ex, lines)
    ex.items = extract_items(lines, table_idx)
    _cross_check(ex)
    return ex


# ---------------------------------------------------------------------
def _extract_gstin(ex, lines, buyer_idx, table_idx, my_gstin):
    candidates = []  # (line_index, gstin, ok, line)
    for i, l in enumerate(lines):
        for g, ok in find_gstins(l):
            candidates.append((i, g, ok, l))
    if not candidates:
        return
    others = [c for c in candidates if not (c[1] == my_gstin or _near(c[1], my_gstin))]
    if not others:
        ex.notes.append("Only your own GSTIN was found on this bill.")
        return
    in_buyer = (lambda i: buyer_idx is not None and i >= buyer_idx
                and (table_idx is None or i < table_idx))
    supplier_side = [c for c in others if not in_buyer(c[0])]
    pool = supplier_side or ([] if my_gstin else others)
    if not pool:
        ex.notes.append("The only other GSTIN on the bill is in the 'Bill To' section, "
                        "so it was not used as the supplier GSTIN.")
        return
    i, g, ok, l = pool[0]
    unique = {c[1] for c in pool}
    if ok and len(unique) == 1 and (supplier_side or my_gstin):
        conf = "high"
    elif ok:
        conf = "medium"
    else:
        conf = "low"
        ex.notes.append(f"GSTIN {g} failed the checksum test - probably mis-read.")
    ex.fields["supplier_gstin"] = FieldResult(g, conf, l)


def _plausible_name(line: str) -> bool:
    letters = sum(ch.isalpha() for ch in line)
    return 3 <= len(line) <= 70 and letters / max(len(line), 1) >= 0.6


def _extract_supplier(ex, lines, buyer_idx, my_gstin, my_name):
    limit = buyer_idx if buyer_idx is not None else min(len(lines), 12)
    gst_line = None
    g = ex.fields["supplier_gstin"]
    if g.value:
        gst_line = next((i for i, l in enumerate(lines) if g.source == l), None)

    def not_me(s):
        return not (my_name and my_name in s.lower())

    # 1. explicit label "Supplier: ABC Traders"
    for l in lines[:max(limit, 1) + 5]:
        m = _SUPPLIER_LABEL_RE.match(l)
        if m and _plausible_name(m.group(1).strip()) and not_me(m.group(1)):
            ex.fields["supplier_name"] = FieldResult(_tidy_name(m.group(1)), "high", l)
            return
    # 2. a company-looking line in the header (before the buyer block)
    header = lines[:max(limit, 1)]
    for i, l in enumerate(header):
        name = re.sub(r"(?i)^\s*(m\s*/\s*s\.?\s*)", "", l)
        if (_COMPANY_RE.search(name) and _plausible_name(name) and not_me(name)
                and not re.search(r"(?i)gstin|invoice|phone|email|address|bank", name)):
            conf = "high" if i <= 3 or (gst_line is not None and 0 <= gst_line - i <= 4) else "medium"
            ex.fields["supplier_name"] = FieldResult(_tidy_name(name), conf, l)
            return
    # 3. first plausible line at the top - a guess
    for l in header[:6]:
        if _plausible_name(l) and not _HEADER_WORDS.search(l) and not_me(l):
            ex.fields["supplier_name"] = FieldResult(_tidy_name(l), "low", l)
            return


_KEEP_CAPS = {"PVT", "LTD", "AND", "THE", "CO", "CO."}


def _tidy_name(s: str) -> str:
    s = re.sub(r"(?i)^\s*m\s*/\s*s\.?\s*", "", s.strip())
    s = re.split(r"\s{2,}|\s+(?:GSTIN|Ph|Phone|Mob)\b", s)[0].strip(" ,.-:")
    letters = [c for c in s if c.isalpha()]
    if letters and all(c.isupper() for c in letters):  # "ABC TRADERS" -> "ABC Traders"
        s = " ".join(w if (len(w) <= 3 and w.isalpha() and w not in _KEEP_CAPS) or "&" in w
                     else w.capitalize() for w in s.split())
    return s


def _extract_invoice_number(ex, lines):
    for i, l in enumerate(lines):
        for m in _INV_NO_RE.finditer(l):
            val = m.group(1).strip(".-/")
            if re.search(r"\d", val) and not re.fullmatch(r"(?i)date.*", val):
                ex.fields["invoice_number"] = FieldResult(val, "high", l)
                return
        if _INV_NO_LABEL_ONLY.search(l) and i + 1 < len(lines):
            tok = lines[i + 1].split()[0] if lines[i + 1].split() else ""
            if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9\-/_]{0,24}", tok) and re.search(r"\d", tok):
                ex.fields["invoice_number"] = FieldResult(tok, "medium", lines[i + 1])
                return


def _extract_date(ex, lines):
    def pick(l, after_label):
        tail = l[after_label:] if after_label is not None else l
        ds = find_dates(tail)
        return ds[0] if ds else None

    # 1. "Invoice Date: 05/10/2026" (also look at the next line)
    for i, l in enumerate(lines):
        m = _INV_DATE_LABEL.search(l)
        if m:
            got = pick(l, m.end()) or (pick(lines[i + 1], None) if i + 1 < len(lines) else None)
            if got:
                d, amb = got
                ex.fields["invoice_date"] = FieldResult(d, "medium" if amb else "high", l)
                return
    # 2. plain "Date: ..." that is not a due/order/e-way date
    for l in lines:
        m = _PLAIN_DATE_LABEL.search(l)
        if m and not _OTHER_DATE_LABEL.search(l[:m.end()]):
            got = pick(l, m.end())
            if got:
                d, amb = got
                ex.fields["invoice_date"] = FieldResult(d, "medium" if amb else "high", l)
                return
    # 3. any date near the top -> a guess
    for l in lines[:15]:
        if _OTHER_DATE_LABEL.search(l):
            continue
        ds = find_dates(l)
        if ds:
            ex.fields["invoice_date"] = FieldResult(ds[0][0], "low", l)
            return


def _extract_amounts(ex, lines, table_idx):
    start = table_idx or 0
    for name, rx in _KEYS.items():
        best = None
        for i, l in enumerate(lines):
            if i < start and name != "taxable_amount":
                continue
            if not rx.search(l) or "GSTIN" in l.upper():
                continue
            if name != "taxable_amount" and _KEYS["taxable_amount"].search(l) and \
                    not re.match(rx, l):
                continue  # e.g. a 'Taxable value | CGST | SGST' table header
            vals = amounts_in(l[rx.search(l).end():])
            vals = [v for v in vals if v >= 0]
            if not vals:
                continue
            starts = bool(re.match(r"^\W*(total\s*)?" + rx.pattern, l, re.I))
            score = (2 if starts else 1, i)
            if best is None or score >= best[0]:
                best = (score, vals[-1], l)
        if best:
            ex.fields[name] = FieldResult(best[1], "medium", best[2])

    best = None
    for i, l in enumerate(lines):
        if _NOT_TOTAL.search(l):
            continue
        for rx, prio in _TOTAL_KEYS:
            m = rx.search(l)
            if m:
                vals = [v for v in amounts_in(l[m.end():]) if v > 0]
                if vals:
                    score = (prio, i)
                    if best is None or score > best[0]:
                        best = (score, vals[-1], l)
                break
    if best:
        ex.fields["total_amount"] = FieldResult(best[1], "medium", best[2])

    ro = None
    for l in lines:
        if _ROUND_RE.search(l):
            vals = amounts_in(l[_ROUND_RE.search(l).end():])
            if vals:
                ro = vals[-1]
                if re.search(r"\(-\)|-\s*\d|less", l, re.I):
                    ro = -abs(ro)
    ex._round_off = ro


def _extract_payment(ex, lines):
    text = "\n".join(lines).lower()
    if re.search(r"\b(partially\s*paid|part\s*payment|partly\s*paid)\b", text):
        ex.fields["payment_status"] = FieldResult("partially_paid", "medium", "partially paid")
    elif re.search(r"(balance\s*due|amount\s*due)\s*[:\-]?\s*(rs\.?|₹)?\s*0(\.0+)?\b", text) or \
            re.search(r"(?<!un)(?<!to be )\b(fully\s*paid|payment\s*received|paid\s*in\s*full)\b", text) or \
            re.search(r"^\s*paid\s*$", text, re.M):
        ex.fields["payment_status"] = FieldResult("paid", "medium", "paid mark on bill")
    elif re.search(r"\b(unpaid|payment\s*pending|balance\s*due|amount\s*due)\b", text):
        ex.fields["payment_status"] = FieldResult("unpaid", "medium", "due mark on bill")


def _close(a, b, tol=1.0):
    return a is not None and b is not None and abs(a - b) <= tol


def _cross_check(ex):
    f = ex.fields
    t, c, s, ig, tot = (f[k].value for k in ("taxable_amount", "cgst_amount", "sgst_amount",
                                             "igst_amount", "total_amount"))
    ro = getattr(ex, "_round_off", None) or 0.0
    if c is not None and s is not None and not _close(c, s, 0.05):
        ex.notes.append(f"CGST ({c}) and SGST ({s}) are normally equal - please check.")
    elif t is not None and tot is not None:
        tax = (c or 0) + (s or 0) + (ig or 0)
        if _close(t + tax + ro, tot, 1.01):
            for k in AMOUNT_FIELDS:
                if f[k].value is not None:
                    f[k].confidence = "high"
        else:
            ex.notes.append(f"Taxable {t:,.2f} + GST {tax:,.2f} does not equal the total "
                            f"{tot:,.2f} - please check the amounts.")
    if tot is None and t is not None:
        tax = (c or 0) + (s or 0) + (ig or 0)
        ex.notes.append(f"Invoice total not found on the bill (taxable + GST would be "
                        f"{t + tax:,.2f}). Please type the total yourself.")
    if c is not None and ig is not None and c > 0 and ig > 0:
        ex.notes.append("Both CGST and IGST were found - a bill normally has one or the other.")
        f["igst_amount"].confidence = f["cgst_amount"].confidence = "low"
    # items vs taxable
    if ex.items and t is not None:
        items_sum = sum(i["taxable_amount"] or 0 for i in ex.items)
        if not _close(items_sum, t, 1.01):
            ex.notes.append(f"Line items add up to {items_sum:,.2f}, taxable value is {t:,.2f} - "
                            "some item rows may be missing or mis-read.")
    if hasattr(ex, "_round_off"):
        del ex._round_off


# =====================================================================
#  Line items
# =====================================================================
_NUM_TOKEN = re.compile(r"-?\d{1,3}(?:,\d{2,3})+(?:\.\d+)?|-?\d+(?:\.\d+)?")
_DIGITISH = str.maketrans({"O": "0", "o": "0", "S": "5", "s": "5", "I": "1", "l": "1", "B": "8"})


def _classify(tok: str):
    """-> (kind, value) where kind is num / pct / unit / numunit / word."""
    t = tok.strip(",|")
    if re.fullmatch(r"\d+(\.\d+)?%", t):
        return "pct", float(t[:-1])
    if _NUM_TOKEN.fullmatch(t):
        return "num", float(t.replace(",", ""))
    if _UNIT_RE.match(t.strip(".")):
        return "unit", t.strip(".").title()
    m = re.fullmatch(r"([0-9OoSsIl.]*\d[0-9OoSsIl.]*)([A-Za-z]{1,7})\.?", t)  # "10Nos", "S0Bags"
    if m and _UNIT_RE.match(m.group(2)):
        try:
            return "numunit", (float(m.group(1).translate(_DIGITISH)), m.group(2).title())
        except ValueError:
            pass
    return "word", t


def parse_item_line(line: str) -> dict | None:
    toks = line.replace("₹", " ").split()
    if len(toks) >= 4 and re.fullmatch(r"\d{1,3}[.)]?", toks[0]):
        toks = toks[1:]                                    # serial number
    cls = [_classify(t) for t in toks]
    # numbers with their token index (a "10Nos" token gives a number + unit)
    nums = [(i, v if k == "num" else v[0]) for i, (k, v) in enumerate(cls) if k in ("num", "numunit")]
    if len(nums) < 2:
        return None
    # HSN = first plain 4/6/8-digit integer that comes after some words
    hsn_i = None
    for i, (k, _v) in enumerate(cls):
        if k == "num" and re.fullmatch(r"\d{4}|\d{6}|\d{8}", toks[i].strip(",|")) and \
                any(c[0] == "word" for c in cls[:i]):
            hsn_i = i
            break
    after = [(i, v) for i, v in nums if hsn_i is None or i > hsn_i]
    if len(after) < 2:
        return None
    amount_i, amount = after[-1]
    qty = rate = None
    qty_i = None
    # rightmost pair with qty x rate = amount
    for a in range(len(after) - 2, -1, -1):
        for b in range(a - 1, -1, -1):
            q, r = after[b][1], after[a][1]
            if q and r and _close(q * r, amount, max(1.0, amount * 0.005)):
                qty, rate, qty_i = q, r, after[b][0]
                break
        if qty is not None:
            break
    confident = qty is not None
    if hsn_i is None and not confident:
        return None                       # without HSN accept only rows whose maths checks out
    if not confident and len(after) >= 3:
        qty_i, qty, rate = after[0][0], after[0][1], after[1][1]
    desc_end = hsn_i if hsn_i is not None else (qty_i if qty_i is not None else amount_i)
    desc = " ".join(t for t, c in zip(toks[:desc_end], cls[:desc_end])).strip(" -.|")
    if not re.search(r"[A-Za-z]", desc):
        return None
    unit = next((v if k == "unit" else v[1] for k, v in cls[(qty_i or 0):amount_i]
                 if k in ("unit", "numunit")), None)
    gst = next((v for k, v in cls if k == "pct"), None)
    if gst is None:
        gst = next((v for i, v in after[:-1] if v in (0.25, 3, 5, 12, 18, 28)
                    and v not in (qty, rate)), None)
    return {"description": desc, "hsn_code": toks[hsn_i] if hsn_i is not None else None,
            "quantity": qty, "unit": unit, "rate": rate, "taxable_amount": amount,
            "gst_rate": gst,
            "confidence": "high" if confident and hsn_i is not None else
                          ("medium" if confident else "low")}


def extract_items(lines: list[str], table_idx: int | None) -> list[dict]:
    if table_idx is None:
        return []
    items = []
    for l in lines[table_idx + 1:]:
        it = parse_item_line(l)
        if it is None and _TOTALS_START_RE.search(l):
            if items:
                break
            continue
        if it:
            it["line_no"] = len(items) + 1
            items.append(it)
    return items


def extraction_from_dict(d: dict) -> Extraction:
    """Rebuild an Extraction saved in the database (invoices.extraction column)."""
    ex = Extraction()
    for k, v in (d or {}).get("fields", {}).items():
        if k in ex.fields:
            val = v.get("value")
            if k == "invoice_date" and isinstance(val, str):
                try:
                    val = date.fromisoformat(val)
                except ValueError:
                    val = None
            ex.fields[k] = FieldResult(val, v.get("confidence", "low"), v.get("source", ""))
    ex.items = (d or {}).get("items", [])
    ex.notes = (d or {}).get("notes", [])
    ex.parser = (d or {}).get("parser", "rules")
    return ex
