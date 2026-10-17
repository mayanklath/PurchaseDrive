"""OCR: turns a photo or PDF of a bill into plain text.

* PDFs that already contain text (most computer-generated bills) are read directly
  with PyMuPDF - fast and 100% accurate.
* Scanned PDFs and photos are cleaned with OpenCV (rotation, shadows, contrast,
  skew) and then read with Tesseract OCR.
"""
from __future__ import annotations

import io
from dataclasses import dataclass, field

import numpy as np

from core.config import find_tesseract


class OCRUnavailable(Exception):
    pass


@dataclass
class OCRResult:
    text: str
    confidence: float | None          # 0-100 (None = text layer, i.e. exact)
    method: str                       # "pdf-text" | "tesseract"
    pages: int = 1
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------
def _tesseract():
    import pytesseract

    cmd = find_tesseract()
    if not cmd:
        raise OCRUnavailable(
            "Tesseract OCR is not installed. Install it (see SETUP_GUIDE.md, Stage 2) "
            "or enter the invoice details manually."
        )
    pytesseract.pytesseract.tesseract_cmd = cmd
    return pytesseract


def tesseract_available() -> bool:
    try:
        _tesseract().get_tesseract_version()
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------
#  Image clean-up with OpenCV
# ---------------------------------------------------------------------
def load_image(data: bytes) -> np.ndarray:
    """Bytes -> OpenCV BGR image, applying the phone's EXIF rotation."""
    import cv2
    from PIL import Image, ImageOps

    pil = Image.open(io.BytesIO(data))
    pil = ImageOps.exif_transpose(pil).convert("RGB")
    return cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)


def _deskew(gray: np.ndarray) -> np.ndarray:
    import cv2

    inv = cv2.bitwise_not(gray)
    _, th = cv2.threshold(inv, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    coords = np.column_stack(np.where(th > 0))
    if len(coords) < 500:
        return gray
    angle = cv2.minAreaRect(coords.astype(np.float32))[-1]
    # OpenCV returns angles in (0, 90]; convert to a small correction angle
    if angle > 45:
        angle -= 90
    if abs(angle) < 0.3 or abs(angle) > 15:
        return gray
    h, w = gray.shape
    m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    return cv2.warpAffine(gray, m, (w, h), flags=cv2.INTER_CUBIC,
                          borderMode=cv2.BORDER_REPLICATE)


def preprocess(img: np.ndarray) -> np.ndarray:
    """Greyscale -> upscale small photos -> remove shadows -> deskew -> black & white."""
    import cv2

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    h, w = gray.shape
    if w < 1600:  # Tesseract likes ~300 dpi text
        scale = 1600 / w
        gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    elif w > 3500:
        scale = 3500 / w
        gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    # shadow / uneven lighting removal: divide by a blurred background estimate
    bg = cv2.medianBlur(cv2.dilate(gray, np.ones((7, 7), np.uint8)), 31)
    norm = cv2.divide(gray, bg, scale=255)
    norm = cv2.fastNlMeansDenoising(norm, None, h=10, templateWindowSize=7, searchWindowSize=21)
    norm = _deskew(norm)
    _, bw = cv2.threshold(norm, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    return bw


def _fix_orientation(pytesseract, img: np.ndarray) -> np.ndarray:
    """Rotate pages photographed sideways/upside down (uses Tesseract OSD if available)."""
    import cv2

    try:
        osd = pytesseract.image_to_osd(img, config="--psm 0",
                                       output_type=pytesseract.Output.DICT)
        rot = int(osd.get("rotate", 0))
        if osd.get("orientation_conf", 0) < 2:
            return img
    except Exception:
        return img
    if rot == 90:
        return cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)
    if rot == 180:
        return cv2.rotate(img, cv2.ROTATE_180)
    if rot == 270:
        return cv2.rotate(img, cv2.ROTATE_90_COUNTERCLOCKWISE)
    return img


def _ocr_with_conf(pytesseract, img: np.ndarray, psm: int) -> tuple[str, float]:
    data = pytesseract.image_to_data(
        img, lang="eng", config=f"--oem 3 --psm {psm} -c preserve_interword_spaces=1",
        output_type=pytesseract.Output.DICT)
    lines: dict[tuple, list[str]] = {}
    confs = []
    for i, word in enumerate(data["text"]):
        word = (word or "").strip()
        if not word:
            continue
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        lines.setdefault(key, []).append(word)
        c = float(data["conf"][i])
        if c >= 0:
            confs.append(c)
    text = "\n".join(" ".join(ws) for _, ws in sorted(lines.items()))
    return text, (sum(confs) / len(confs) if confs else 0.0)


def ocr_image_array(img: np.ndarray) -> tuple[str, float]:
    pytesseract = _tesseract()
    img = _fix_orientation(pytesseract, img)
    clean = preprocess(img)
    text, conf = _ocr_with_conf(pytesseract, clean, psm=6)
    if conf < 70:  # try another page-layout mode and keep the better one
        text2, conf2 = _ocr_with_conf(pytesseract, clean, psm=4)
        if conf2 > conf:
            text, conf = text2, conf2
    return text, conf


# ---------------------------------------------------------------------
#  PDFs
# ---------------------------------------------------------------------
def _pdf_page_to_bgr(page, dpi: int = 300) -> np.ndarray:
    import cv2

    pix = page.get_pixmap(dpi=dpi)
    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    if pix.n == 4:
        return cv2.cvtColor(arr, cv2.COLOR_RGBA2BGR)
    if pix.n == 1:
        return cv2.cvtColor(arr, cv2.COLOR_GRAY2BGR)
    return cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)


def ocr_pdf(data: bytes, max_pages: int = 10) -> OCRResult:
    import pymupdf as fitz  # PyMuPDF

    doc = fitz.open(stream=data, filetype="pdf")
    texts, confs, method, warnings = [], [], "pdf-text", []
    for i, page in enumerate(doc):
        if i >= max_pages:
            warnings.append(f"Only the first {max_pages} pages were read.")
            break
        layer = page.get_text("text", sort=True).strip()
        if len(layer) >= 40:
            texts.append(layer)
            continue
        method = "tesseract"  # scanned page
        t, c = ocr_image_array(_pdf_page_to_bgr(page))
        texts.append(t)
        confs.append(c)
    pages = doc.page_count
    doc.close()
    conf = (sum(confs) / len(confs)) if confs else None
    return OCRResult("\n\n".join(texts), conf, method, pages, warnings)


def run_ocr(data: bytes, mime: str) -> OCRResult:
    if mime == "application/pdf":
        return ocr_pdf(data)
    text, conf = ocr_image_array(load_image(data))
    return OCRResult(text, conf, "tesseract", 1)


# ---------------------------------------------------------------------
#  Previews (work on iPhone too, because PDFs are shown as images)
# ---------------------------------------------------------------------
def pdf_preview_images(data: bytes, max_pages: int = 5, dpi: int = 110) -> list[bytes]:
    import pymupdf as fitz

    doc = fitz.open(stream=data, filetype="pdf")
    out = []
    for i, page in enumerate(doc):
        if i >= max_pages:
            break
        out.append(page.get_pixmap(dpi=dpi).tobytes("png"))
    doc.close()
    return out


def pdf_page_count(data: bytes) -> int:
    import pymupdf as fitz

    with fitz.open(stream=data, filetype="pdf") as doc:
        return doc.page_count
