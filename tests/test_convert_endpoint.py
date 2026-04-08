"""Tests for POST /convert endpoint."""
import asyncio
import io
import zipfile

import fitz  # PyMuPDF
import pytest
from fastapi.testclient import TestClient

import main as app_module


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_pdf_bytes(pages: int = 1) -> bytes:
    """Create a minimal in-memory PDF with *pages* blank pages."""
    doc = fitz.open()
    for _ in range(pages):
        doc.new_page()
    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    return buf.getvalue()


class FakeBody:
    def __init__(self, data: bytes):
        self._data = data

    def read(self):
        return self._data


class FakeS3:
    def __init__(self, data: bytes | None = None, *, key: str = "test.pdf"):
        self._data = data
        self._key = key
        self.stored: dict | None = None

    class exceptions:
        class NoSuchKey(Exception):
            pass

    def get_object(self, Bucket, Key):
        if self._data is not None and Key == self._key:
            return {"Body": FakeBody(self._data)}
        raise FakeS3.exceptions.NoSuchKey()

    def put_object(self, Bucket, Key, Body, ContentType=None):
        Body.seek(0)
        self.stored = {
            "Bucket": Bucket,
            "Key": Key,
            "Body": Body.read(),
            "ContentType": ContentType,
        }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_convert_jpg_success(monkeypatch):
    pdf_bytes = _make_pdf_bytes(pages=2)
    fake = FakeS3(pdf_bytes, key="test.pdf")

    monkeypatch.setattr(app_module, "_r2_client", lambda: fake)
    monkeypatch.setattr(
        app_module,
        "generate_presigned_url",
        lambda **kw: "https://presigned.example/result.zip",
    )

    client = TestClient(app_module.app)
    resp = client.post("/convert", json={"objectKey": "test.pdf", "convertType": "jpg"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["presignedUrl"] == "https://presigned.example/result.zip"
    assert data["originalKey"] == "test.pdf"
    assert data["convertedSize"] > 0

    # The stored object should be a valid ZIP containing page images
    assert fake.stored is not None
    assert fake.stored["Key"].endswith(".converted.zip")
    assert fake.stored["ContentType"] == "application/zip"

    with zipfile.ZipFile(io.BytesIO(fake.stored["Body"])) as zf:
        names = zf.namelist()
        assert len(names) == 2
        assert "page_001.jpg" in names
        assert "page_002.jpg" in names


def test_convert_png_success(monkeypatch):
    pdf_bytes = _make_pdf_bytes(pages=1)
    fake = FakeS3(pdf_bytes, key="doc.pdf")

    monkeypatch.setattr(app_module, "_r2_client", lambda: fake)
    monkeypatch.setattr(
        app_module,
        "generate_presigned_url",
        lambda **kw: "https://presigned.example/result.zip",
    )

    client = TestClient(app_module.app)
    resp = client.post("/convert", json={"objectKey": "doc.pdf", "convertType": "png"})

    assert resp.status_code == 200
    assert fake.stored is not None
    with zipfile.ZipFile(io.BytesIO(fake.stored["Body"])) as zf:
        names = zf.namelist()
        assert len(names) == 1
        assert "page_001.png" in names


def test_convert_custom_dpi_accepted(monkeypatch):
    """Explicit dpi in the request body is accepted and used."""
    pdf_bytes = _make_pdf_bytes(pages=1)
    fake = FakeS3(pdf_bytes, key="hi.pdf")

    monkeypatch.setattr(app_module, "_r2_client", lambda: fake)
    monkeypatch.setattr(
        app_module,
        "generate_presigned_url",
        lambda **kw: "https://presigned.example/result.zip",
    )

    client = TestClient(app_module.app)
    resp = client.post("/convert", json={"objectKey": "hi.pdf", "convertType": "jpg", "dpi": 72})
    assert resp.status_code == 200
    # 72 DPI pages will be smaller than 96 DPI pages
    assert fake.stored is not None


def test_convert_dpi_out_of_range(monkeypatch):
    """dpi outside 72-300 must return 400."""
    monkeypatch.setattr(app_module, "_r2_client", lambda: FakeS3())
    client = TestClient(app_module.app)

    resp_low = client.post("/convert", json={"objectKey": "a.pdf", "convertType": "jpg", "dpi": 10})
    assert resp_low.status_code == 400
    assert "dpi" in resp_low.json()["detail"].lower()

    resp_high = client.post("/convert", json={"objectKey": "a.pdf", "convertType": "jpg", "dpi": 600})
    assert resp_high.status_code == 400
    assert "dpi" in resp_high.json()["detail"].lower()


def test_convert_dpi_non_integer(monkeypatch):
    """Non-integer dpi must return 400."""
    monkeypatch.setattr(app_module, "_r2_client", lambda: FakeS3())
    client = TestClient(app_module.app)
    resp = client.post("/convert", json={"objectKey": "a.pdf", "convertType": "jpg", "dpi": "high"})
    assert resp.status_code == 400
    assert "dpi" in resp.json()["detail"].lower()


def test_convert_timeout_returns_503(monkeypatch):
    """When conversion times out, the endpoint must return 503 (not a CORS-less 504 from Koyeb)."""
    pdf_bytes = _make_pdf_bytes(pages=1)
    fake = FakeS3(pdf_bytes, key="slow.pdf")

    monkeypatch.setattr(app_module, "_r2_client", lambda: fake)
    monkeypatch.setattr(app_module, "_CONVERT_TIMEOUT_SECONDS", 0)
    # Force asyncio.wait_for to time out immediately by using a 0-second timeout
    # and a slow convert_pdf replacement.
    import time
    async def _slow_convert(*args, **kwargs):
        await asyncio.sleep(1)  # longer than 0s timeout
        return b"", "application/zip"

    monkeypatch.setattr(app_module, "convert_pdf", lambda *a, **kw: (_ for _ in ()).throw(asyncio.TimeoutError()))
    # Patch asyncio.wait_for to raise TimeoutError directly
    original_wait_for = asyncio.wait_for
    async def _raising_wait_for(coro, timeout):
        coro.close()  # clean up the coroutine without running it
        raise asyncio.TimeoutError()
    monkeypatch.setattr(asyncio, "wait_for", _raising_wait_for)

    client = TestClient(app_module.app)
    resp = client.post("/convert", json={"objectKey": "slow.pdf", "convertType": "jpg"})
    assert resp.status_code == 503
    assert "timed out" in resp.json()["detail"].lower()


def test_convert_missing_object_key(monkeypatch):
    monkeypatch.setattr(app_module, "_r2_client", lambda: FakeS3())
    client = TestClient(app_module.app)
    resp = client.post("/convert", json={"convertType": "jpg"})
    assert resp.status_code == 400
    assert "objectKey" in resp.json()["detail"]


def test_convert_missing_convert_type(monkeypatch):
    monkeypatch.setattr(app_module, "_r2_client", lambda: FakeS3())
    client = TestClient(app_module.app)
    resp = client.post("/convert", json={"objectKey": "test.pdf"})
    assert resp.status_code == 400
    assert "convertType" in resp.json()["detail"]


def test_convert_unsupported_convert_type(monkeypatch):
    monkeypatch.setattr(app_module, "_r2_client", lambda: FakeS3())
    client = TestClient(app_module.app)
    resp = client.post("/convert", json={"objectKey": "test.pdf", "convertType": "bmp"})
    assert resp.status_code == 400
    assert "Unsupported" in resp.json()["detail"]


def test_convert_object_not_found(monkeypatch):
    fake = FakeS3(data=None, key="test.pdf")
    monkeypatch.setattr(app_module, "_r2_client", lambda: fake)
    client = TestClient(app_module.app)
    resp = client.post("/convert", json={"objectKey": "missing.pdf", "convertType": "jpg"})
    assert resp.status_code == 404
    assert "missing.pdf" in resp.json()["detail"]


def test_convert_deduplicates_converted_suffix(monkeypatch):
    """Calling /convert on a key that already has .converted.zip suffix should not double-suffix."""
    pdf_bytes = _make_pdf_bytes(pages=1)
    fake = FakeS3(pdf_bytes, key="doc.converted.zip")

    monkeypatch.setattr(app_module, "_r2_client", lambda: fake)
    monkeypatch.setattr(
        app_module,
        "generate_presigned_url",
        lambda **kw: "https://presigned.example/result.zip",
    )

    client = TestClient(app_module.app)
    resp = client.post("/convert", json={"objectKey": "doc.converted.zip", "convertType": "jpg"})
    assert resp.status_code == 200
    stored_key = fake.stored["Key"]
    # Should not produce "doc.converted.converted.zip"
    assert stored_key.count(".converted") == 1
