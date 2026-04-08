# Standard imports
import io
import logging
import os
import tempfile
from typing import List, Tuple

import fitz  # PyMuPDF
from PIL import Image

log = logging.getLogger(__name__)

# Default raster DPI for image export (96 is fine for web; raise to 150–300 for print)
DEFAULT_DPI = 96



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


def convert_pdf(
    input_bytes: bytes,
    convert_type: str,
    languages: List[str] | None = None,
    ocr_engine: str | None = None,
    dpi: int = DEFAULT_DPI,
) -> Tuple[bytes, str]:
    """Convert PDF to page images (jpg/png) and return a ZIP.

    Returns (bytes, content_type).
    """
    convert_type = convert_type.lower()
    if convert_type not in ("jpg", "png"):
        raise ValueError(f"Unsupported convert_type: {convert_type}")

    doc = fitz.open(stream=input_bytes, filetype="pdf")
    try:
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
    finally:
        doc.close()
