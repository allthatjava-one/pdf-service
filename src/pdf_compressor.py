"""
PDF compression using PyMuPDF (fitz) + Pillow.

Two-phase strategy
------------------
Phase 1 — Image re-encoding (biggest win for image-heavy PDFs):
  For every embedded image larger than MIN_IMAGE_BYTES:
  - Downscale if the longest edge exceeds MAX_IMAGE_DIM pixels.
  - Re-encode as JPEG at JPEG_QUALITY (DCTDecode).
  - Replace via page.replace_image() — the official PyMuPDF API that handles
    all xref dictionary updates (Filter, Width, Height, ColorSpace, etc.)
    correctly, avoiding "Insufficient data for an image" errors that occur
    with manual xref manipulation.
  - Images with a soft-mask (separate alpha channel) are skipped to
    avoid visual corruption.
  - 1-bit / JBIG2 / CCITT fax images are skipped (JPEG not meaningful).

Phase 2 — Structural compression:
  Re-save with PyMuPDF garbage=4 (removes unreferenced objects, deduplicates
  streams) and zlib deflate on text/font streams.
  Note: deflate_images is intentionally omitted — images are already handled
  in Phase 1, and applying deflate_images on top of DCTDecode images can
  cause double-compression artefacts.

Tuning constants (top of file):
  MAX_IMAGE_DIM  — longest edge cap in pixels        (default: 1500)
  JPEG_QUALITY   — JPEG quality 0-95, lower = smaller (default: 75)
  MIN_IMAGE_BYTES — skip images smaller than this     (default: 20 KB)
"""

from __future__ import annotations

import io
import logging

import fitz  # PyMuPDF
from PIL import Image

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tuning constants
# ---------------------------------------------------------------------------
MAX_IMAGE_DIM = 1500      # downscale if longest edge exceeds this (pixels)
JPEG_QUALITY = 75         # JPEG quality; 0-95 — lower is smaller + lossier
MIN_IMAGE_BYTES = 20_480  # 20 KB — skip images smaller than this

# Image formats not suitable for JPEG re-encoding
_SKIP_EXTS = {"jb2", "jbig2", "ccitt"}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compress_pdf(input_bytes: bytes) -> bytes:
    """Compress *input_bytes* (raw PDF) and return the compressed PDF bytes.

    Returns the original bytes unchanged if no reduction was achieved.

    Raises:
        ValueError: If *input_bytes* is empty or not a valid PDF.
    """
    if not input_bytes:
        raise ValueError("Input PDF bytes must not be empty.")
    if input_bytes.lstrip()[:4] != b"%PDF":
        raise ValueError("Input does not appear to be a valid PDF.")

    doc = fitz.open(stream=input_bytes, filetype="pdf")

    if doc.is_encrypted:
        doc.close()
        return input_bytes

    # Phase 1: re-encode embedded images
    _recompress_images(doc)

    # Phase 2: structural / stream compression
    # deflate_images intentionally omitted — images already handled above;
    # applying it after manual replacement risks double-compression corruption.
    result = doc.tobytes(
        garbage=4,
        deflate=True,
        deflate_fonts=True,
        clean=True,
    )
    doc.close()

    orig_size = len(input_bytes)
    new_size = len(result)
    if orig_size:
        pct = (orig_size - new_size) / orig_size * 100
        log.info("PDF compression: %d → %d bytes (%.1f%% reduction)", orig_size, new_size, pct)

    return result if new_size < orig_size else input_bytes


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _recompress_images(doc: fitz.Document) -> None:
    """Re-encode every embedded image in *doc* in-place."""
    seen: set[int] = set()
    for page in doc:
        for img_info in page.get_images(full=True):
            xref = img_info[0]
            if xref in seen:
                continue
            seen.add(xref)
            try:
                _recompress_one(doc, page, xref)
            except Exception as exc:
                log.debug("Skipping image xref=%d: %s", xref, exc)


def _recompress_one(doc: fitz.Document, page: fitz.Page, xref: int) -> None:
    """Re-encode a single image xref as JPEG when it produces a smaller stream."""
    base = doc.extract_image(xref)
    if not base:
        return

    orig_bytes: bytes = base["image"]
    if len(orig_bytes) < MIN_IMAGE_BYTES:
        return

    # Skip images with a soft-mask (separate alpha channel) to avoid
    # visual corruption when converting to opaque JPEG.
    if base.get("smask", 0) != 0:
        return

    # Skip bi-level / fax-compressed images — JPEG is not meaningful for them
    if base.get("ext", "").lower() in _SKIP_EXTS:
        return

    try:
        img = Image.open(io.BytesIO(orig_bytes))
    except Exception:
        return  # unsupported format — leave untouched

    # Skip 1-bit bilevel images
    if img.mode == "1":
        return

    w, h = img.size
    needs_resize = w > MAX_IMAGE_DIM or h > MAX_IMAGE_DIM

    if needs_resize:
        img.thumbnail((MAX_IMAGE_DIM, MAX_IMAGE_DIM), Image.LANCZOS)

    # Flatten RGBA → RGB (composited over white) so JPEG can encode it
    if img.mode == "RGBA":
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[3])
        img = bg
    elif img.mode not in ("RGB", "L"):
        img = img.convert("RGB")

    out = io.BytesIO()
    img.save(out, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    jpeg_bytes = out.getvalue()

    # Skip if no size saving and no resize
    if len(jpeg_bytes) >= len(orig_bytes) and not needs_resize:
        return

    # Use the official PyMuPDF API — it handles all xref dictionary updates
    # (Filter, Width, Height, ColorSpace, BitsPerComponent, DecodeParms, Length)
    # correctly, including edge cases like array-form filters and DecodeParms arrays.
    page.replace_image(xref, stream=jpeg_bytes)

    log.debug("Re-encoded xref=%d: %d → %d bytes", xref, len(orig_bytes), len(jpeg_bytes))

