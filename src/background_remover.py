"""Background remover utility.

This prefers the `rembg` library for high-quality segmentation. If
`rembg` is not available in the environment, it falls back to a simple
corner-color heuristic implemented with Pillow.
"""
from __future__ import annotations

import io
import logging
from typing import Tuple

from PIL import Image, ImageStat

log = logging.getLogger(__name__)


def _get_rembg_remove():
    """Try to import and return the `rembg.remove` function.

    This import is performed lazily so that installing `rembg` can be
    deferred until an administrator explicitly triggers it.

    Returns:
        callable: `rembg.remove` when available

    Raises:
        ImportError: when `rembg` is not installed in the environment
    """
    import importlib

    try:
        rembg = importlib.import_module("rembg")
        return getattr(rembg, "remove")
    except Exception as exc:  # pragma: no cover - environment dependent
        raise ImportError("rembg is not available") from exc


def _sample_corner_color(img: Image.Image, box_size: int = 6) -> Tuple[int, int, int]:
    w, h = img.size
    boxes = [
        (0, 0, box_size, box_size),
        (w - box_size, 0, w, box_size),
        (0, h - box_size, box_size, h),
        (w - box_size, h - box_size, w, h),
    ]
    rs = []
    gs = []
    bs = []
    for b in boxes:
        crop = img.crop(b).convert("RGB")
        stat = ImageStat.Stat(crop)
        r, g, b_ = stat.mean
        rs.append(r)
        gs.append(g)
        bs.append(b_)

    return int(sum(rs) / len(rs)), int(sum(gs) / len(gs)), int(sum(bs) / len(bs))


def _color_distance(c1: Tuple[int, int, int], c2: Tuple[int, int, int]) -> float:
    return ((c1[0] - c2[0]) ** 2 + (c1[1] - c2[1]) ** 2 + (c1[2] - c2[2]) ** 2) ** 0.5


def _heuristic_remove(image_bytes: bytes, threshold: int = 60) -> bytes:
    with Image.open(io.BytesIO(image_bytes)) as im:
        im = im.convert("RGBA")
        bg_color = _sample_corner_color(im, box_size=max(4, min(im.size) // 10))
        pixels = im.load()
        w, h = im.size

        for y in range(h):
            for x in range(w):
                r, g, b, a = pixels[x, y]
                if a == 0:
                    continue
                if _color_distance((r, g, b), bg_color) <= threshold:
                    pixels[x, y] = (r, g, b, 0)

        out = io.BytesIO()
        im.save(out, format="PNG")
        return out.getvalue()


def remove_background_image(
    image_bytes: bytes,
    type: str = "remove",
    quality: str = "medium",
    blur_strength: str = "medium",
    threshold: int = 60,
) -> bytes:
    """Process an image to either remove or blur the background.

    Args:
        image_bytes: input image bytes
        type: 'remove' to make background transparent, 'blur' to blur background
        quality: 'low'|'medium'|'high' — controls final filter
        blur_strength: 'light'|'medium'|'strong' — controls blur radius when type=='blur'
        threshold: fallback heuristic threshold (used when rembg unavailable)

    Returns:
        Processed image bytes. For 'remove' returns PNG bytes with transparency.
        For 'blur' returns bytes in the same format as the input image.
    """
    quality = (quality or "medium").lower()
    proc_type = (type or "remove").lower()
    blur_strength = (blur_strength or "medium").lower()
    log.info("Processing image with type=%s, quality=%s, blur_strength=%s", proc_type, quality, blur_strength)

    # helper to map blur strength to radius
    blur_map = {"light": 5, "medium": 15, "strong": 30}
    radius = blur_map.get(blur_strength, 15)

    # obtain a foreground mask via rembg or heuristic (lazy import)
    try:
        try:
            _rembg_remove = _get_rembg_remove()
            fg_png = _rembg_remove(image_bytes)
        except ImportError:
            log.info("rembg not available; using heuristic segmentation")
            fg_png = _heuristic_remove(image_bytes, threshold=threshold)
    except Exception as exc:
        log.warning("background segmentation failed (%s), falling back to heuristic", exc)
        fg_png = _heuristic_remove(image_bytes, threshold=threshold)

    # Load images
    with Image.open(io.BytesIO(fg_png)).convert("RGBA") as fg_img:
        # original image to preserve format for 'blur'
        with Image.open(io.BytesIO(image_bytes)) as orig_im:
            orig_mode = orig_im.mode
            orig_format = (orig_im.format or "PNG").upper()
            # create mask from alpha channel
            mask = fg_img.split()[3]

            if proc_type == "remove":
                log.info("Removing background (transparent output)")
                # ensure final is PNG with alpha
                final = fg_img
                # quality control
                from PIL import ImageFilter

                if quality == "high":
                    # stronger, tunable sharpening using UnsharpMask + enhancement
                    from PIL import ImageEnhance

                    final = final.filter(ImageFilter.UnsharpMask(radius=2, percent=150, threshold=3))
                    final = ImageEnhance.Sharpness(final).enhance(1.3)
                elif quality == "low":
                    final = final.filter(ImageFilter.SMOOTH)

                out = io.BytesIO()
                final.save(out, format="PNG")
                return out.getvalue()

            elif proc_type == "blur":
                log.info("Blurring background (preserving original format)")
                # blur the original background
                from PIL import ImageFilter

                # create blurred background
                bg = orig_im.convert("RGBA").filter(ImageFilter.GaussianBlur(radius))
                # paste foreground onto blurred background (preserves alpha)
                final = bg.copy()
                final.paste(fg_img, (0, 0), fg_img)

                # apply quality filter
                if quality == "high":
                    # final = final.filter(ImageFilter.SHARPEN)  # basic sharpen is often too aggressive, so use UnsharpMask for tunable sharpening
                    # stronger sharpening for blurred outputs as well
                    from PIL import ImageEnhance

                    final = final.filter(ImageFilter.UnsharpMask(radius=2, percent=150, threshold=3))
                    final = ImageEnhance.Sharpness(final).enhance(1.3)
                elif quality == "low":
                    final = final.filter(ImageFilter.SMOOTH)

                out = io.BytesIO()
                # Save in same format as original. If original was JPEG, remove alpha by compositing
                if orig_format in ("JPEG", "JPG"):
                    rgb = Image.new("RGB", final.size, (255, 255, 255))
                    # use alpha channel as mask to composite onto white background
                    alpha = final.split()[3]
                    rgb.paste(final.convert("RGB"), (0, 0), alpha)
                    rgb.save(out, format="JPEG")
                else:
                    final.save(out, format=orig_format)
                return out.getvalue()

            else:
                raise ValueError(f"Unsupported type: {proc_type}")
