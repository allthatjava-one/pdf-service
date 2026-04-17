"""Integration tests for POST /split endpoint."""
import io

import fitz  # PyMuPDF
import pytest

pytest.skip("/split endpoint disabled — tests skipped", allow_module_level=True)

from fastapi.testclient import TestClient

import main as app_module


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_pdf_bytes(pages: int = 3) -> bytes:
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


class FakeBody:
    def __init__(self, data: bytes):
        self._data = data

    def read(self):
        return self._data


class FakeS3:
    """Fake R2/S3 client that supports multiple put_object calls."""

    def __init__(self, data: bytes | None = None, *, key: str = "test.pdf"):
        self._data = data
        self._key = key
        self.stored: dict[str, dict] = {}  # Key -> stored payload

    class exceptions:
        class NoSuchKey(Exception):
            pass

    def get_object(self, Bucket, Key):
        if self._data is not None and Key == self._key:
            return {"Body": FakeBody(self._data)}
        raise FakeS3.exceptions.NoSuchKey()

    def put_object(self, Bucket, Key, Body, ContentType=None):
        Body.seek(0)
        self.stored[Key] = {
            "Bucket": Bucket,
            "Key": Key,
            "Body": Body.read(),
            "ContentType": ContentType,
        }


def _presigned_url(object_key: str) -> str:
    return f"https://presigned.example/{object_key}"


# ---------------------------------------------------------------------------
# outputOption=MULTIPLE tests
# ---------------------------------------------------------------------------

def test_split_multiple_single_pages(monkeypatch):
    pdf = _make_pdf_bytes(pages=5)
    fake = FakeS3(pdf, key="doc.pdf")

    monkeypatch.setattr(app_module, "_r2_client", lambda: fake)
    monkeypatch.setattr(
        app_module, "generate_presigned_url",
        lambda **kw: _presigned_url(kw["object_key"]),
    )

    client = TestClient(app_module.app)
    resp = client.post(
        "/split",
        json={"objectKey": "doc.pdf", "splitOption": "1,3,5", "outputOption": "MULTIPLE"},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["outputOption"] == "MULTIPLE"
    results = data["results"]
    assert len(results) == 3
    assert results[0]["segment"] == "1"
    assert results[1]["segment"] == "3"
    assert results[2]["segment"] == "5"
    # Each split key should be stored in R2
    for item in results:
        key = item["splitKey"]
        assert key in fake.stored
        assert fake.stored[key]["ContentType"] == "application/pdf"
        assert _page_count(fake.stored[key]["Body"]) == 1


def test_split_multiple_range(monkeypatch):
    pdf = _make_pdf_bytes(pages=5)
    fake = FakeS3(pdf, key="doc.pdf")

    monkeypatch.setattr(app_module, "_r2_client", lambda: fake)
    monkeypatch.setattr(
        app_module, "generate_presigned_url",
        lambda **kw: _presigned_url(kw["object_key"]),
    )

    client = TestClient(app_module.app)
    resp = client.post(
        "/split",
        json={"objectKey": "doc.pdf", "splitOption": "1-3", "outputOption": "MULTIPLE"},
    )

    assert resp.status_code == 200
    data = resp.json()
    results = data["results"]
    assert len(results) == 1
    assert results[0]["segment"] == "1-3"
    stored_key = results[0]["splitKey"]
    assert _page_count(fake.stored[stored_key]["Body"]) == 3


def test_split_multiple_mixed_segments(monkeypatch):
    pdf = _make_pdf_bytes(pages=7)
    fake = FakeS3(pdf, key="doc.pdf")

    monkeypatch.setattr(app_module, "_r2_client", lambda: fake)
    monkeypatch.setattr(
        app_module, "generate_presigned_url",
        lambda **kw: _presigned_url(kw["object_key"]),
    )

    client = TestClient(app_module.app)
    resp = client.post(
        "/split",
        json={"objectKey": "doc.pdf", "splitOption": "1,3,5-7", "outputOption": "MULTIPLE"},
    )

    assert resp.status_code == 200
    data = resp.json()
    results = data["results"]
    assert len(results) == 3
    assert results[2]["segment"] == "5-7"
    assert _page_count(fake.stored[results[2]["splitKey"]]["Body"]) == 3


def test_split_multiple_default_output_option(monkeypatch):
    """outputOption defaults to MULTIPLE when omitted."""
    pdf = _make_pdf_bytes(pages=3)
    fake = FakeS3(pdf, key="doc.pdf")

    monkeypatch.setattr(app_module, "_r2_client", lambda: fake)
    monkeypatch.setattr(
        app_module, "generate_presigned_url",
        lambda **kw: _presigned_url(kw["object_key"]),
    )

    client = TestClient(app_module.app)
    resp = client.post("/split", json={"objectKey": "doc.pdf", "splitOption": "1,2"})

    assert resp.status_code == 200
    assert resp.json()["outputOption"] == "MULTIPLE"


# ---------------------------------------------------------------------------
# outputOption=ONE tests
# ---------------------------------------------------------------------------

def test_split_one_multiple_segments(monkeypatch):
    pdf = _make_pdf_bytes(pages=5)
    fake = FakeS3(pdf, key="doc.pdf")

    monkeypatch.setattr(app_module, "_r2_client", lambda: fake)
    monkeypatch.setattr(
        app_module, "generate_presigned_url",
        lambda **kw: _presigned_url(kw["object_key"]),
    )

    client = TestClient(app_module.app)
    resp = client.post(
        "/split",
        json={"objectKey": "doc.pdf", "splitOption": "1,3,5", "outputOption": "ONE"},
    )

    assert resp.status_code == 200
    data = resp.json()
    # ONE: combined into single PDF, returned as single-entry results list
    assert data["success"] is True
    assert data["outputOption"] == "ONE"
    results = data["results"]
    assert len(results) == 1
    item = results[0]
    assert item["segment"] == "combined"
    assert "url" in item
    combined_key = item["splitKey"]
    assert combined_key.endswith("-split-combined.pdf")
    assert combined_key in fake.stored
    assert fake.stored[combined_key]["ContentType"] == "application/pdf"
    # Combined PDF should contain all 3 pages
    assert _page_count(fake.stored[combined_key]["Body"]) == 3


def test_split_one_single_segment(monkeypatch):
    """ONE with a single-segment splitOption should still return a presignedUrl."""
    pdf = _make_pdf_bytes(pages=3)
    fake = FakeS3(pdf, key="doc.pdf")

    monkeypatch.setattr(app_module, "_r2_client", lambda: fake)
    monkeypatch.setattr(
        app_module, "generate_presigned_url",
        lambda **kw: _presigned_url(kw["object_key"]),
    )

    client = TestClient(app_module.app)
    resp = client.post(
        "/split",
        json={"objectKey": "doc.pdf", "splitOption": "2-3", "outputOption": "ONE"},
    )

    assert resp.status_code == 200
    data = resp.json()
    # ONE with a single segment: still wrapped in results list
    assert data["outputOption"] == "ONE"
    results = data["results"]
    assert len(results) == 1
    item = results[0]
    assert item["segment"] == "combined"
    assert "url" in item
    key = item["splitKey"]
    assert _page_count(fake.stored[key]["Body"]) == 2


# ---------------------------------------------------------------------------
# Validation / error cases
# ---------------------------------------------------------------------------

def test_missing_object_key(monkeypatch):
    monkeypatch.setattr(app_module, "_r2_client", lambda: FakeS3())
    client = TestClient(app_module.app)
    resp = client.post("/split", json={"splitOption": "1", "outputOption": "MULTIPLE"})
    assert resp.status_code == 400
    assert "objectKey" in resp.json()["detail"]


def test_missing_split_option(monkeypatch):
    monkeypatch.setattr(app_module, "_r2_client", lambda: FakeS3())
    client = TestClient(app_module.app)
    resp = client.post("/split", json={"objectKey": "doc.pdf", "outputOption": "MULTIPLE"})
    assert resp.status_code == 400
    assert "splitOption" in resp.json()["detail"]


def test_invalid_output_option(monkeypatch):
    monkeypatch.setattr(app_module, "_r2_client", lambda: FakeS3())
    client = TestClient(app_module.app)
    resp = client.post(
        "/split",
        json={"objectKey": "doc.pdf", "splitOption": "1", "outputOption": "INVALID"},
    )
    assert resp.status_code == 400
    assert "outputOption" in resp.json()["detail"]


def test_object_not_found(monkeypatch):
    fake = FakeS3(None)  # get_object always raises NoSuchKey
    monkeypatch.setattr(app_module, "_r2_client", lambda: fake)
    client = TestClient(app_module.app)
    resp = client.post(
        "/split",
        json={"objectKey": "missing.pdf", "splitOption": "1", "outputOption": "MULTIPLE"},
    )
    assert resp.status_code == 404


def test_invalid_split_option_letters(monkeypatch):
    pdf = _make_pdf_bytes(pages=3)
    fake = FakeS3(pdf, key="doc.pdf")
    monkeypatch.setattr(app_module, "_r2_client", lambda: fake)
    client = TestClient(app_module.app)
    resp = client.post(
        "/split",
        json={"objectKey": "doc.pdf", "splitOption": "abc", "outputOption": "MULTIPLE"},
    )
    assert resp.status_code == 400
    assert "split" in resp.json()["detail"].lower()


def test_invalid_split_option_out_of_range(monkeypatch):
    pdf = _make_pdf_bytes(pages=3)
    fake = FakeS3(pdf, key="doc.pdf")
    monkeypatch.setattr(app_module, "_r2_client", lambda: fake)
    client = TestClient(app_module.app)
    resp = client.post(
        "/split",
        json={"objectKey": "doc.pdf", "splitOption": "99", "outputOption": "MULTIPLE"},
    )
    assert resp.status_code == 400
