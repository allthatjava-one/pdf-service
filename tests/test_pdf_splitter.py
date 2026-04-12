"""Unit tests for src/pdf_splitter.py."""
import io

import fitz  # PyMuPDF
import pytest

from src.pdf_splitter import split_pdf


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_pdf_bytes(pages: int = 3) -> bytes:
    """Create a minimal in-memory PDF with *pages* blank pages."""
    doc = fitz.open()
    for _ in range(pages):
        doc.new_page()
    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    return buf.getvalue()


def _page_count(pdf_bytes: bytes) -> int:
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    count = doc.page_count
    doc.close()
    return count


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_single_page():
    pdf = _make_pdf_bytes(pages=3)
    result = split_pdf(pdf, "2")
    assert len(result) == 1
    label, seg_bytes = result[0]
    assert label == "2"
    assert _page_count(seg_bytes) == 1


def test_range_segment():
    pdf = _make_pdf_bytes(pages=5)
    result = split_pdf(pdf, "2-4")
    assert len(result) == 1
    label, seg_bytes = result[0]
    assert label == "2-4"
    assert _page_count(seg_bytes) == 3


def test_multi_segment_single_pages():
    pdf = _make_pdf_bytes(pages=5)
    result = split_pdf(pdf, "1,3,5")
    assert len(result) == 3
    labels = [label for label, _ in result]
    assert labels == ["1", "3", "5"]
    for _, seg_bytes in result:
        assert _page_count(seg_bytes) == 1


def test_multi_segment_mixed():
    pdf = _make_pdf_bytes(pages=7)
    result = split_pdf(pdf, "1,3,5-7")
    assert len(result) == 3
    assert result[0][0] == "1"
    assert _page_count(result[0][1]) == 1
    assert result[1][0] == "3"
    assert _page_count(result[1][1]) == 1
    assert result[2][0] == "5-7"
    assert _page_count(result[2][1]) == 3


def test_full_document_range():
    pdf = _make_pdf_bytes(pages=4)
    result = split_pdf(pdf, "1-4")
    assert len(result) == 1
    assert _page_count(result[0][1]) == 4


def test_single_page_document():
    pdf = _make_pdf_bytes(pages=1)
    result = split_pdf(pdf, "1")
    assert len(result) == 1
    assert _page_count(result[0][1]) == 1


# ---------------------------------------------------------------------------
# Validation errors
# ---------------------------------------------------------------------------

def test_out_of_range_page():
    pdf = _make_pdf_bytes(pages=3)
    with pytest.raises(ValueError, match="out of range"):
        split_pdf(pdf, "5")


def test_out_of_range_end_of_range():
    pdf = _make_pdf_bytes(pages=3)
    with pytest.raises(ValueError, match="out of range"):
        split_pdf(pdf, "2-10")


def test_inverted_range():
    pdf = _make_pdf_bytes(pages=5)
    with pytest.raises(ValueError, match="start page must be"):
        split_pdf(pdf, "5-2")


def test_malformed_option_letters():
    pdf = _make_pdf_bytes(pages=3)
    with pytest.raises(ValueError):
        split_pdf(pdf, "abc")


def test_malformed_option_partial():
    pdf = _make_pdf_bytes(pages=3)
    with pytest.raises(ValueError):
        split_pdf(pdf, "1a")


def test_empty_split_option():
    pdf = _make_pdf_bytes(pages=3)
    with pytest.raises(ValueError, match="must not be empty"):
        split_pdf(pdf, "")


def test_whitespace_split_option():
    pdf = _make_pdf_bytes(pages=3)
    with pytest.raises(ValueError, match="must not be empty"):
        split_pdf(pdf, "   ")


def test_empty_bytes():
    with pytest.raises(ValueError, match="must not be empty"):
        split_pdf(b"", "1")


def test_non_pdf_bytes():
    with pytest.raises(ValueError, match="valid PDF"):
        split_pdf(b"not a pdf at all", "1")


def test_encrypted_pdf():
    doc = fitz.open()
    doc.new_page()
    buf = io.BytesIO()
    doc.save(
        buf,
        encryption=fitz.PDF_ENCRYPT_AES_256,
        owner_pw="owner",
        user_pw="user",
    )
    doc.close()
    with pytest.raises(ValueError, match="[Ee]ncrypted"):
        split_pdf(buf.getvalue(), "1")
