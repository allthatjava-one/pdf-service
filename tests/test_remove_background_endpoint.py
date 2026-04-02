import io

from fastapi.testclient import TestClient
from PIL import Image

import main as app_module


class FakeBody:
    def __init__(self, data: bytes):
        self._data = data

    def read(self):
        return self._data


class FakeS3:
    def __init__(self, data: bytes):
        self._data = data
        self.stored = None

    class exceptions:
        class NoSuchKey(Exception):
            pass

    def get_object(self, Bucket, Key):
        if Key == "test-image.png":
            return {"Body": FakeBody(self._data)}
        raise self.exceptions.NoSuchKey()

    def put_object(self, Bucket, Key, Body, ContentType=None):
        # read body bytes for assertions
        Body.seek(0)
        self.stored = {"Bucket": Bucket, "Key": Key, "Body": Body.read(), "ContentType": ContentType}


def _make_png_bytes():
    im = Image.new("RGB", (40, 40), (10, 200, 10))
    for x in range(10, 30):
        for y in range(10, 30):
            im.putpixel((x, y), (200, 10, 10))
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def test_remove_background_endpoint(monkeypatch):
    img_bytes = _make_png_bytes()
    fake = FakeS3(img_bytes)

    # monkeypatch r2 client factory and presigned URL generator
    monkeypatch.setattr(app_module, "_r2_client", lambda: fake)
    monkeypatch.setattr(app_module, "generate_presigned_url", lambda **kwargs: "https://presigned.example/download")

    client = TestClient(app_module.app)
    resp = client.post("/remove-background", json={"objectKey": "test-image.png"})
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("success") is True
    assert data.get("presignedUrl") == "https://presigned.example/download"
    assert fake.stored is not None
    assert fake.stored["Key"].endswith("-bg-removed.png")
    # stored body should be PNG
    assert fake.stored["Body"].startswith(b"\x89PNG")

    # Now test blur type with quality and blur-strength params
    resp2 = client.post(
        "/remove-background",
        json={"objectKey": "test-image.png", "type": "blur", "quality": "high", "blur-strength": "light"},
    )
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert data2.get("success") is True
    # stored body updated
    assert fake.stored is not None
    assert fake.stored["Key"].endswith("-bg-blurred.png")
    assert fake.stored["Body"].startswith(b"\x89PNG")
