# Standard imports
import io
import logging
import os
import tempfile
from typing import List, Tuple

import fitz  # PyMuPDF
from PIL import Image
from src.utils.ocr_backends import ocr_image_to_string

log = logging.getLogger(__name__)

# Default raster DPI for OCR rendering
dpi = 150



def _langs_to_ocr(langs: List[str] | None) -> List[str]:
    if not langs:
        return ["en"]
    mapped = []
    for l in langs:
        code = l.lower()
        if code in ("en", "eng"):
            mapped.append("en")
        elif code in ("fr", "fra", "fre"):
            mapped.append("fr")
        elif code in ("ko", "kor"):
            mapped.append("ko")
        else:
            mapped.append(code)
    return mapped


def convert_pdf(input_bytes: bytes, convert_type: str, languages: List[str] | None = None, ocr_engine: str | None = None) -> Tuple[bytes, str]:
    """Convert PDF to text, images (zip), or searchable PDF.

    Returns (bytes, content_type).
    """
    convert_type = convert_type.lower()
    if convert_type not in ("text", "jpg", "png"):
        raise ValueError(f"Unsupported convert_type: {convert_type}")

    lang_codes = _langs_to_ocr(languages)

    def _ensure_ocr_available():
        # No-op here: ocr_backends will raise if no backend is available when invoked
        return True

    doc = fitz.open(stream=input_bytes, filetype="pdf")
    try:
        if convert_type == "text":
            out_lines = []
            for page in doc:
                text = page.get_text("text")
                if not text.strip():
                    # OCR fallback using available OCR backend
                    _ensure_ocr_available()
                    pix = page.get_pixmap(dpi=dpi)
                    img_bytes = pix.tobytes("png")
                    try:
                        ocr = ocr_image_to_string(img_bytes, languages=lang_codes)
                    finally:
                        try:
                            del pix
                        except Exception:
                            pass
                    out_lines.append(ocr)
                else:
                    out_lines.append(text)
            result = "\n\f\n".join(out_lines)
            return result.encode("utf-8"), "text/plain"

        if convert_type in ("jpg", "png"):
            import zipfile

            bio = io.BytesIO()
            with zipfile.ZipFile(bio, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
                for i, page in enumerate(doc, start=1):
                    pix = page.get_pixmap(dpi=dpi)
                    fmt = "jpeg" if convert_type == "jpg" else "png"
                    img_bytes = pix.tobytes(fmt)
                    name = f"page_{i:03d}.{convert_type}"
                    zf.writestr(name, img_bytes)
                    try:
                        del pix
                    except Exception:
                        pass
            return bio.getvalue(), "application/zip"

        # searchable_pdf support removed — this function now supports only
        # text extraction and page image exports (jpg/png as a ZIP).

    finally:
        doc.close()
