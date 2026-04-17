"""
PDF merger disabled.

The original implementation is preserved below as a literal string for
reference but is not executed.
"""

_ORIGINAL_IMPL = '''
from __future__ import annotations

import fitz  # PyMuPDF


def merge_pdfs(input_pdfs: list[bytes]) -> bytes:
    """Merge multiple PDF byte streams and return a single PDF document."""
    if len(input_pdfs) < 2:
        raise ValueError("At least two PDF files are required for merging.")

    merged_doc = fitz.open()

    try:
        for index, pdf_bytes in enumerate(input_pdfs, start=1):
            if not pdf_bytes:
                raise ValueError(f"Input PDF #{index} is empty.")
            if pdf_bytes.lstrip()[:4] != b"%PDF":
                raise ValueError(f"Input PDF #{index} does not appear to be a valid PDF.")

            source_doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            try:
                if source_doc.is_encrypted:
                    raise ValueError(f"Input PDF #{index} is encrypted and cannot be merged.")
                merged_doc.insert_pdf(source_doc)
            finally:
                source_doc.close()  # free immediately after inserting

        if merged_doc.page_count == 0:
            raise ValueError("Merged PDF is empty.")

        return merged_doc.tobytes(garbage=4, deflate=True, deflate_fonts=True, clean=True)
    finally:
        merged_doc.close()
'''
