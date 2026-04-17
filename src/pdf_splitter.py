"""
PDF splitter disabled.

The original implementation has been retained below as a literal string
for reference but is not executed.
"""

_ORIGINAL_IMPL = """
"""

_ORIGINAL_IMPL = '''
"""
PDF splitting using PyMuPDF (fitz).

Accepts a ``splitOption`` string such as ``"1,3,5-7"`` and returns one PDF
byte-string per parsed segment.  Pages are specified in 1-based numbering.

Supported split-option formats
-------------------------------
- Single page:  ``"3"``       → page 3
- Range:        ``"5-7"``     → pages 5, 6, 7  (inclusive)
- Multi-segment:``"1,3,5-7"`` → three segments in order

Notes
-----
- Overlapping or duplicate segments are allowed — each produces a separate output PDF.
- Pages within a range are preserved in document order.
- Inverted ranges (e.g. ``"7-5"``) are rejected.
"""

from __future__ import annotations

import re

import fitz  # PyMuPDF

# Matches a single page number (digits only) or a range (digits-digits).
_SEGMENT_RE = re.compile(r"^\d+(?:-\d+)?$")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _parse_split_option(split_option: str, page_count: int) -> list[tuple[str, list[int]]]:
    """Return a list of ``(label, [0-based page indices])`` tuples.

    Args:
        split_option: User-supplied option string, e.g. ``"1,3,5-7"``.
        page_count:   Total number of pages in the source document.

    Raises:
        ValueError: On empty string, non-integer tokens, inverted ranges, or
                    out-of-range page numbers.
    """
    raw_segments = [s.strip() for s in split_option.split(",") if s.strip()]
    if not raw_segments:
        raise ValueError("splitOption must not be empty.")

    results: list[tuple[str, list[int]]] = []
    for seg in raw_segments:
        if not _SEGMENT_RE.match(seg):
            raise ValueError(
                f"Invalid segment '{seg}'. Each segment must be a page number "
                "or a range in the form 'start-end' (e.g. '5-7')."
            )

        if "-" in seg:
            start_str, end_str = seg.split("-", 1)
            start, end = int(start_str), int(end_str)
            if start < 1 or end < 1:
                raise ValueError(f"Page numbers must be >= 1 in segment '{seg}'.")
            if start > end:
                raise ValueError(
                    f"Invalid range '{seg}': start page must be <= end page."
                )
            if end > page_count:
                raise ValueError(
                    f"Page {end} is out of range (document has {page_count} page(s))."
                )
            results.append((seg, list(range(start - 1, end))))
        else:
            page_num = int(seg)
            if page_num < 1:
                raise ValueError(f"Page numbers must be >= 1, got '{seg}'.")
            if page_num > page_count:
                raise ValueError(
                    f"Page {page_num} is out of range (document has {page_count} page(s))."
                )
            results.append((seg, [page_num - 1]))

    return results


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def split_pdf(input_bytes: bytes, split_option: str) -> list[tuple[str, bytes]]:
    """Split *input_bytes* according to *split_option*.

    Args:
        input_bytes:  Raw PDF bytes.
        split_option: Comma-separated page numbers / ranges, e.g. ``"1,3,5-7"``.

    Returns:
        List of ``(segment_label, pdf_bytes)`` in the same order as the parsed
        segments.  Each element is a standalone, valid PDF document.

    Raises:
        ValueError: If *input_bytes* is empty or not a valid PDF, the document is
                    encrypted, or *split_option* contains invalid / out-of-range
                    page references.
    """
    if not input_bytes:
        raise ValueError("Input PDF bytes must not be empty.")
    if input_bytes.lstrip()[:4] != b"%PDF":
        raise ValueError("Input does not appear to be a valid PDF.")

    doc = fitz.open(stream=input_bytes, filetype="pdf")
    try:
        if doc.is_encrypted:
            raise ValueError("Encrypted PDFs cannot be split.")

        segments = _parse_split_option(split_option, doc.page_count)

        results: list[tuple[str, bytes]] = []
        for label, page_indices in segments:
            seg_doc = fitz.open()
            try:
                for idx in page_indices:
                    seg_doc.insert_pdf(doc, from_page=idx, to_page=idx)
                seg_bytes = seg_doc.tobytes(garbage=4, deflate=True)
            finally:
                seg_doc.close()
            results.append((label, seg_bytes))

        return results
    finally:
        doc.close()
'''

