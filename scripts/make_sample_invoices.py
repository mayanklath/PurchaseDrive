"""Creates fictional sample purchase bills for testing PurchaseDrive AI.

Run from the project folder:
    python scripts/make_sample_invoices.py

Files are written to the  sample_invoices/  folder:
  01_ABC_Traders_INV-1023.pdf        computer-made PDF (CGST + SGST), 3 items, unpaid
  02_XYZ_Enterprises_photo.jpg       phone-style photo (tilted, shadow), paid
  03_Shree_Ganesh_Steel_IGST.png     inter-state bill with IGST
  04_ABC_Traders_scanned.pdf         scanned PDF (image only, needs Tesseract)
  05_ABC_Traders_INV-1023_copy.jpg   the SAME bill as 01 photographed -> duplicate test
  06_Krishna_Hardware_partial.pdf    partially paid bill, no line-item HSN codes

All companies and GSTINs are made up (the GSTINs have valid check digits).
For testing, put  06AAACD1234E1ZD  (printed on the bills as the buyer GSTIN) into
Settings -> "My business GSTIN" so the app never mistakes it for the supplier.
"""
from __future__ import annotations

import io
import os
import random
import sys

from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(os.path.dirname(HERE), "sample_invoices")
sys.path.insert(0, os.path.dirname(HERE))

from core.extractor import _GST_CHARS  # noqa: E402


def gstin(body14: str) -> str:
    total = 0
    for i, ch in enumerate(body14):
        p = _GST_CHARS.index(ch) * (2 if i % 2 else 1)
        total += p // 36 + p % 36
    return body14 + _GST_CHARS[(36 - total % 36) % 36]


BUYER = ("Demo Buyer Pvt Ltd", "Plot 12, Sector 18, Gurugram, Haryana 122015",
         gstin("06AAACD1234E1Z"))

INVOICES = [
    dict(file="01_ABC_Traders_INV-1023.pdf", kind="pdf",
         supplier="ABC Traders", addr="45 Main Bazar, Sonipat, Haryana 131001",
         gstin=gstin("06ABCPT1234K1Z"), no="INV-1023", date="05/10/2026",
         items=[("Portland Cement 53 Grade", "2523", 50, "Bags", 380.00, 18),
                ("TMT Steel Bar 12mm", "7214", 200, "Kg", 62.50, 18),
                ("Binding Wire", "7217", 10, "Kg", 95.00, 18)],
         tax="intra", footer="Payment Status: Unpaid"),
    dict(file="02_XYZ_Enterprises_photo.jpg", kind="photo",
         supplier="XYZ Enterprises", addr="Shop 7, Old Railway Road, Gurugram, Haryana",
         gstin=gstin("06AAJFX5678L1Z"), no="XYZ/26-27/0457", date="12-Oct-2026",
         items=[("LED Bulb 9W", "8539", 100, "Pcs", 65.00, 12),
                ("PVC Wire 1.5 sq mm (90m)", "8544", 5, "Rolls", 1450.00, 18)],
         tax="intra", footer="PAID"),
    dict(file="03_Shree_Ganesh_Steel_IGST.png", kind="png",
         supplier="Shree Ganesh Steel Pvt Ltd", addr="MIDC Bhosari, Pune, Maharashtra 411026",
         gstin=gstin("27AACCS9876M1Z"), no="SGS-8841", date="03/11/2026",
         items=[("MS Angle 50x50x5", "7216", 500, "Kg", 58.00, 18),
                ("MS Plate 6mm", "7208", 300, "Kg", 61.00, 18)],
         tax="inter", footer="Payment Status: Unpaid"),
    dict(file="04_ABC_Traders_scanned.pdf", kind="scanpdf",
         supplier="ABC Traders", addr="45 Main Bazar, Sonipat, Haryana 131001",
         gstin=gstin("06ABCPT1234K1Z"), no="INV-0988", date="18/09/2026",
         items=[("Wall Putty 40kg", "3214", 20, "Bags", 720.00, 18)],
         tax="intra", footer=""),
    dict(file="05_ABC_Traders_INV-1023_copy.jpg", kind="photo", same_as=0),
    dict(file="06_Krishna_Hardware_partial.pdf", kind="pdf",
         supplier="Krishna Hardware Stores", addr="Sadar Bazar, Delhi 110006",
         gstin=gstin("07AAKFK4321P1Z"), no="KH-2210", date="20/10/2026",
         items=[("Door Hinges 4 inch", None, 48, "Pcs", 35.00, 18),
                ("Tower Bolt 6 inch", None, 24, "Pcs", 55.00, 18)],
         tax="inter", footer="Partially Paid - Advance received Rs. 1,000.00"),
]


def money(v: float) -> str:
    s = f"{v:,.2f}"
    return s


def build_lines(inv: dict) -> list[tuple[float, float, str, int, bool]]:
    """(x_mm, y_mm, text, size_pt, bold) for an A4 page."""
    L = []
    y = 15
    L.append((15, y, inv["supplier"].upper(), 16, True)); y += 8
    L.append((15, y, inv["addr"], 9, False)); y += 5
    L.append((15, y, f"GSTIN: {inv['gstin']}    Ph: 98XXXXXX10", 9, False)); y += 9
    L.append((85, y, "TAX INVOICE", 13, True)); y += 9
    L.append((15, y, f"Invoice No: {inv['no']}", 10, False))
    L.append((120, y, f"Invoice Date: {inv['date']}", 10, False)); y += 9
    L.append((15, y, "Bill To:", 10, True)); y += 5
    L.append((15, y, BUYER[0], 10, False)); y += 5
    L.append((15, y, BUYER[1], 9, False)); y += 5
    L.append((15, y, f"GSTIN: {BUYER[2]}", 9, False)); y += 10
    cols = [15, 25, 95, 115, 135, 160]
    hdr = ["Sr", "Description", "HSN", "Qty", "Rate", "Amount"]
    for x, h in zip(cols, hdr):
        L.append((x, y, h, 10, True))
    y += 7
    taxable = 0.0
    for n, (desc, hsn, qty, unit, rate, _) in enumerate(inv["items"], 1):
        amt = qty * rate
        taxable += amt
        vals = [str(n), desc, hsn or "", f"{qty} {unit}", money(rate), money(amt)]
        for x, v in zip(cols, vals):
            if v:
                L.append((x, y, v, 10, False))
        y += 7
    y += 4
    gst_rate = inv["items"][0][5]
    tax = round(taxable * gst_rate / 100, 2)
    total_exact = taxable + tax
    total = round(total_exact)
    ro = round(total - total_exact, 2)
    L.append((110, y, "Taxable Value", 10, False)); L.append((160, y, money(taxable), 10, False)); y += 6
    if inv["tax"] == "intra":
        half = round(tax / 2, 2)
        L.append((110, y, f"CGST @ {gst_rate / 2:g}%", 10, False)); L.append((160, y, money(half), 10, False)); y += 6
        L.append((110, y, f"SGST @ {gst_rate / 2:g}%", 10, False)); L.append((160, y, money(half), 10, False)); y += 6
    else:
        L.append((110, y, f"IGST @ {gst_rate:g}%", 10, False)); L.append((160, y, money(tax), 10, False)); y += 6
    if abs(ro) >= 0.01:
        L.append((110, y, "Round Off", 10, False)); L.append((160, y, money(ro), 10, False)); y += 6
    L.append((110, y, "Grand Total", 11, True)); L.append((160, y, money(total), 11, True)); y += 12
    if inv["footer"]:
        L.append((15, y, inv["footer"], 11, True)); y += 8
    L.append((15, y, "Bank: State Bank of India  A/c 0000XXXX1234  IFSC SBIN0000123", 8, False)); y += 12
    L.append((130, y, f"For {inv['supplier']}", 9, False)); y += 12
    L.append((130, y, "Authorised Signatory", 9, False))
    return L


def _font(size_px: int, bold: bool):
    names = (["DejaVuSans-Bold.ttf", "arialbd.ttf", "Arial Bold.ttf"] if bold
             else ["DejaVuSans.ttf", "arial.ttf", "Arial.ttf"])
    dirs = ["", "/usr/share/fonts/truetype/dejavu/", r"C:\Windows\Fonts\\", "/Library/Fonts/"]
    for n in names:
        for d in dirs:
            try:
                return ImageFont.truetype(d + n, size_px)
            except OSError:
                continue
    return ImageFont.load_default(size=size_px)


def render_image(lines, dpi=200) -> Image.Image:
    mm = dpi / 25.4
    img = Image.new("RGB", (int(210 * mm), int(297 * mm)), "white")
    d = ImageDraw.Draw(img)
    for x, y, text, size, bold in lines:
        d.text((x * mm, y * mm), text, fill="black", font=_font(int(size * dpi / 72), bold))
    return img


def phone_photo(img: Image.Image, seed: int) -> Image.Image:
    rnd = random.Random(seed)
    w, h = img.size
    # warm paper colour + shadow gradient
    paper = Image.new("RGB", img.size, (238, 232, 218))
    img = Image.blend(img, paper, 0.12)
    grad = Image.linear_gradient("L").resize(img.size).point(lambda p: 255 - p // 4)
    img = Image.composite(img, Image.new("RGB", img.size, (150, 145, 135)), grad)
    img = img.rotate(rnd.uniform(-2.5, 2.5), expand=True, fillcolor=(90, 80, 70))
    img = img.filter(ImageFilter.GaussianBlur(0.8))
    # sensor noise
    noise = Image.effect_noise(img.size, 18).convert("RGB")
    img = Image.blend(img, noise, 0.06)
    return img.resize((int(img.width * 0.75), int(img.height * 0.75)))


def write_pdf(lines, path):
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(path, pagesize=A4)
    H = A4[1]
    for x, y, text, size, bold in lines:
        c.setFont("Helvetica-Bold" if bold else "Helvetica", size)
        c.drawString(x * mm, H - (y * mm) - size * 0.9, text)
    c.save()


def write_scanned_pdf(img: Image.Image, path):
    import pymupdf as fitz

    buf = io.BytesIO()
    img.convert("L").save(buf, "JPEG", quality=70)
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_image(page.rect, stream=buf.getvalue())
    doc.save(path)
    doc.close()


def main():
    os.makedirs(OUT, exist_ok=True)
    for i, inv in enumerate(INVOICES):
        if "same_as" in inv:
            base = INVOICES[inv["same_as"]]
            inv = {**base, "file": inv["file"], "kind": inv["kind"]}
        lines = build_lines(inv)
        path = os.path.join(OUT, inv["file"])
        if inv["kind"] == "pdf":
            write_pdf(lines, path)
        elif inv["kind"] == "photo":
            phone_photo(render_image(lines), i).save(path, "JPEG", quality=85)
        elif inv["kind"] == "png":
            render_image(lines, dpi=170).save(path, "PNG")
        elif inv["kind"] == "scanpdf":
            write_scanned_pdf(phone_photo(render_image(lines), i), path)
        print("created", path)
    print(f"\nBuyer GSTIN printed on all bills (put it in Settings): {BUYER[2]}")


if __name__ == "__main__":
    main()
