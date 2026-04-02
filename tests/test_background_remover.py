import io

from PIL import Image

from src.background_remover import remove_background_image


def _make_test_image() -> bytes:
    # green background with a red square in the center
    im = Image.new("RGB", (80, 80), (0, 180, 0))
    for x in range(20, 60):
        for y in range(20, 60):
            im.putpixel((x, y), (200, 10, 10))
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def test_remove_background_basic():
    data = _make_test_image()
    out = remove_background_image(data, type="remove", quality="medium", threshold=80)
    img = Image.open(io.BytesIO(out)).convert("RGBA")

    # corners should be transparent (background removed)
    assert img.getpixel((0, 0))[3] == 0
    assert img.getpixel((79, 0))[3] == 0
    assert img.getpixel((0, 79))[3] == 0
    assert img.getpixel((79, 79))[3] == 0

    # center (where red square is) should be opaque
    assert img.getpixel((40, 40))[3] == 255


def test_blur_background_basic():
    data = _make_test_image()
    out = remove_background_image(data, type="blur", quality="medium", blur_strength="light", threshold=80)
    img = Image.open(io.BytesIO(out))

    # output for blur should preserve image mode/format and have no alpha requirement
    assert img.size == (80, 80)
