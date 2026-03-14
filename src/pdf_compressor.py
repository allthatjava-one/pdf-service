"""
PDF compression using Python's standard library only (no third-party packages).

Strategy:
  1. Locate every indirect object that contains an uncompressed content stream.
  2. Apply zlib FlateDecode compression to that stream.
  3. Update the stream dictionary (/Filter, /Length).
  4. Rebuild the cross-reference (xref) table so the output file is valid.

Encrypted PDFs are returned unchanged (their streams cannot be modified).
Typical size reduction: 10-40 % on unoptimised PDFs.  PDFs that already use
FlateDecode compression will be returned as-is (no double-compression).
"""

from __future__ import annotations

import io
import re
import zlib

# ---------------------------------------------------------------------------
# Compiled patterns
# ---------------------------------------------------------------------------

# Matches "N G obj" at the start of a line (handles \n and \r\n endings).
_OBJ_RE = re.compile(rb"(?m)^(\d+)\s+(\d+)\s+obj\b")

# Matches the "stream" keyword and its mandatory line terminator.
_STREAM_RE = re.compile(rb"\bstream\r?\n")

# Detects an existing /Filter entry in an object dictionary.
_FILTER_RE = re.compile(rb"/Filter\b")

# Matches "/Length N" for replacement.
_LENGTH_RE = re.compile(rb"/Length\s+\d+")

_ENDOBJ    = b"endobj"
_ENDSTREAM = b"endstream"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compress_pdf(input_bytes: bytes) -> bytes:
    """Compress *input_bytes* (raw PDF) and return the compressed PDF bytes.

    Args:
        input_bytes: Raw bytes of the original PDF file.

    Returns:
        Compressed PDF bytes (or the original bytes if compression is not
        beneficial or the file cannot be processed).

    Raises:
        ValueError: If *input_bytes* is empty or not a valid PDF.
    """
    if not input_bytes:
        raise ValueError("Input PDF bytes must not be empty.")
    if input_bytes.lstrip()[:4] != b"%PDF":
        raise ValueError("Input does not appear to be a valid PDF.")

    # Skip encrypted PDFs — streams are opaque ciphertext.
    if b"/Encrypt" in input_bytes[-4096:]:
        return input_bytes

    data = input_bytes
    objs = list(_OBJ_RE.finditer(data))
    if not objs:
        return data

    out  : io.BytesIO      = io.BytesIO()
    xref : dict[int, int]  = {}  # obj_num -> byte offset in output

    # Write the PDF header (everything before the first indirect object).
    out.write(data[: objs[0].start()])

    for idx, m in enumerate(objs):
        obj_num = int(m.group(1))

        # Search boundary: start of the next object, or end of file.
        limit = objs[idx + 1].start() if idx + 1 < len(objs) else len(data)

        endobj_pos = data.rfind(_ENDOBJ, m.end(), limit)
        if endobj_pos == -1:
            # Malformed object — copy the raw fragment as-is.
            xref[obj_num] = out.tell()
            out.write(data[m.start() : limit])
            continue

        raw_obj   = data[m.start() : endobj_pos + len(_ENDOBJ)]
        processed = _compress_stream(raw_obj)

        xref[obj_num] = out.tell()
        out.write(processed)
        out.write(b"\n")

    # Rebuild the xref table.
    xref_pos = out.tell()
    _write_xref(out, xref)

    # Preserve the original trailer dictionary.
    trailer_m = re.search(rb"trailer\s*(<<.*?>>)", data, re.DOTALL)
    trailer   = trailer_m.group(1) if trailer_m else b"<</Size 1>>"

    out.write(b"trailer\n")
    out.write(trailer)
    out.write(b"\nstartxref\n")
    out.write(str(xref_pos).encode())
    out.write(b"\n%%EOF\n")

    result = out.getvalue()
    # Return the compressed version only if it is actually smaller.
    return result if len(result) < len(input_bytes) else input_bytes


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _compress_stream(obj_bytes: bytes) -> bytes:
    """Return *obj_bytes* with its stream zlib-compressed, or unchanged."""
    sm = _STREAM_RE.search(obj_bytes)
    if not sm:
        return obj_bytes  # No stream in this object.

    es = obj_bytes.rfind(_ENDSTREAM)
    if es == -1:
        return obj_bytes

    dict_part = obj_bytes[: sm.start()]

    # Already has a /Filter — do not double-compress.
    if _FILTER_RE.search(dict_part):
        return obj_bytes

    raw_stream = obj_bytes[sm.end() : es].rstrip(b"\r\n")
    if len(raw_stream) < 32:
        return obj_bytes  # Too small to benefit.

    compressed = zlib.compress(raw_stream, level=9)
    if len(compressed) >= len(raw_stream):
        return obj_bytes  # Compression not beneficial.

    # Update /Length in the dictionary.
    new_dict = _LENGTH_RE.sub(
        b"/Length " + str(len(compressed)).encode(),
        dict_part,
        count=1,
    )

    # Insert /Filter /FlateDecode before the closing >> of the dictionary.
    close = new_dict.rfind(b">>")
    if close == -1:
        return obj_bytes

    new_dict = new_dict[:close] + b"/Filter /FlateDecode\n" + new_dict[close:]

    return (
        new_dict
        + b"stream\n"
        + compressed
        + b"\nendstream\n"
        + obj_bytes[es + len(_ENDSTREAM) :]
    )


def _write_xref(out: io.BytesIO, entries: dict[int, int]) -> None:
    """Write a cross-reference table for all objects in *entries*."""
    if not entries:
        out.write(b"xref\n0 1\n0000000000 65535 f \n")
        return

    max_num = max(entries)
    out.write(b"xref\n")
    out.write(f"0 {max_num + 1}\n".encode())
    out.write(b"0000000000 65535 f \n")  # Object 0 is always free.
    for num in range(1, max_num + 1):
        if num in entries:
            out.write(f"{entries[num]:010d} 00000 n \n".encode())
        else:
            out.write(b"0000000000 65535 f \n")
