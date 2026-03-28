import io
import logging
from typing import List

from PIL import Image

log = logging.getLogger(__name__)

_has_easyocr = False
_easyocr_reader = None

try:
    import easyocr
    import numpy as np

    _has_easyocr = True
except Exception:
    _has_easyocr = False


def _map_langs_for_easyocr(langs: List[str] | None) -> List[str]:
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


def ocr_image_to_string(image_bytes: bytes, languages: List[str] | None = None) -> str:
    """Return recognized text from image bytes using EasyOCR.

    Raises RuntimeError if EasyOCR is not installed.
    """
    if not _has_easyocr:
        raise RuntimeError("EasyOCR is not installed. Install 'easyocr' to enable OCR without system binaries.")

    global _easyocr_reader
    langs = _map_langs_for_easyocr(languages)
    try:
        if _easyocr_reader is None:
            _easyocr_reader = easyocr.Reader(langs, gpu=False)
        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        arr = np.array(img)
        results = _easyocr_reader.readtext(arr)
        texts = [r[1] for r in results if r and r[1].strip()]
        return "\n".join(texts)
    except Exception as exc:
        log.warning("EasyOCR failed: %s", exc)
        raise
