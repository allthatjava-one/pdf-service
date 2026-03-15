"""
Cloudflare Workers entry point for the PDF Compressor Service.

Routing
-------
  POST /compress   — Retrieve a PDF from R2, compress it, store it back, and
                     return a presigned download URL.

Cron trigger
------------
  on_scheduled     — Deletes "-compressed" files older than CLEANUP_MINUTES
                     from R2.  Fired by the Cloudflare cron schedule defined
                     in wrangler.toml under [triggers].

Environment variables / secrets (configure in wrangler.toml / wrangler secrets)
------
  R2_BUCKET             (binding)  — R2 bucket binding
  R2_ACCOUNT_ID         (var)      — Cloudflare account ID
  R2_BUCKET_NAME        (var)      — R2 bucket name (must match the binding)
  ALLOWED_ORIGINS       (var)      — Comma-separated CORS origins, or "*"
  PRESIGNED_URL_EXPIRY  (var)      — Presigned URL lifetime in seconds
  CLEANUP_MINUTES       (var)      — Age threshold (minutes) for compressed-file cleanup
  ADMIN_SECRET          (secret)   — Bearer token required for /admin/* endpoints
  R2_ACCESS_KEY_ID      (secret)   — R2 S3-compat access key
  R2_SECRET_ACCESS_KEY  (secret)   — R2 S3-compat secret key
"""

from __future__ import annotations

import json
import traceback

from js import Response
from pyodide.ffi import to_js
from urllib.parse import urlparse

from cleanup import delete_old_compressed_files
from pdf_compressor import compress_pdf
from presigned_url import generate_presigned_url

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_ALLOWED_METHODS = "POST, OPTIONS"
_ALLOWED_HEADERS = "Content-Type, Authorization"
_DEFAULT_EXPIRY  = 60  # minutes


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------

def _build_cors_headers(request_origin: str, allowed_origins_cfg: str) -> dict[str, str]:
    """Return CORS headers whose Allow-Origin reflects the request origin when permitted."""
    allowed = [o.strip() for o in allowed_origins_cfg.split(",") if o.strip()]
    if "*" in allowed:
        allow_origin = "*"
    elif request_origin in allowed:
        allow_origin = request_origin
    else:
        allow_origin = allowed[0] if allowed else ""

    return {
        "Access-Control-Allow-Origin":  allow_origin,
        "Access-Control-Allow-Methods": _ALLOWED_METHODS,
        "Access-Control-Allow-Headers": _ALLOWED_HEADERS,
        "Access-Control-Max-Age":       "86400",
    }


def _json_response(
    data: dict,
    *,
    status: int = 200,
    extra_headers: dict[str, str] | None = None,
) -> Response:
    body    = json.dumps(data)
    headers = {"content-type": "application/json"}
    if extra_headers:
        headers.update(extra_headers)
    return Response.new(body, to_js({"status": status, "headers": headers}))


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------

def _handle_hello() -> Response:
    """GET /hello — health-check endpoint, open to all origins."""
    return _json_response(
        {"status": "ok", "message": "pdf-compressor-service is running"},
        status=200,
        extra_headers={"Access-Control-Allow-Origin": "*"},
    )


async def _handle_trigger_cleanup(request, env, cors_headers: dict[str, str]) -> Response:
    """POST /admin/trigger-cleanup — manually invoke the scheduled cleanup.

    Requires the Authorization header to match ADMIN_SECRET:
      Authorization: Bearer <ADMIN_SECRET>
    """
    admin_secret = getattr(env, "ADMIN_SECRET", None)
    if not admin_secret:
        return _json_response(
            {"error": "ADMIN_SECRET is not configured"},
            status=500,
            extra_headers=cors_headers,
        )

    auth_header = request.headers.get("Authorization") or ""
    if auth_header != f"Bearer {admin_secret}":
        return _json_response(
            {"error": "Unauthorized"},
            status=401,
            extra_headers=cors_headers,
        )

    print("[INFO] Manual cleanup triggered via /admin/trigger-cleanup")
    try:
        result = await delete_old_compressed_files(env)
        return _json_response(
            {"success": True, "result": result},
            status=200,
            extra_headers=cors_headers,
        )
    except BaseException as exc:
        traceback.print_exc()
        return _json_response(
            {"error": f"Cleanup failed: {exc}"},
            status=500,
            extra_headers=cors_headers,
        )


async def _handle_compress(request, env, cors_headers: dict[str, str]) -> Response:
    """Handle POST /compress."""

    # --- Parse JSON body ---
    try:
        raw_body = await request.text()
        body     = json.loads(raw_body)
    except Exception:
        return _json_response(
            {"error": "Invalid JSON body"},
            status=400,
            extra_headers=cors_headers,
        )

    object_key: str | None = body.get("objectKey") if isinstance(body, dict) else None
    if not object_key or not isinstance(object_key, str) or not object_key.strip():
        return _json_response(
            {"error": "Missing or invalid field: 'objectKey'"},
            status=400,
            extra_headers=cors_headers,
        )

    object_key = object_key.strip()
    print(f"[INFO] /compress request for key: {object_key!r}")

    # --- Retrieve original file from R2 ---
    r2_object = await env.R2_BUCKET.get(object_key)
    if r2_object is None:
        print(f"[WARN] Object not found in R2: {object_key!r}")
        return _json_response(
            {"error": f"Object not found: {object_key}"},
            status=404,
            extra_headers=cors_headers,
        )

    # Convert the R2 ArrayBuffer to Python bytes.
    # Workers Python exposes arrayBuffer() which returns a JS ArrayBuffer;
    # Pyodide's to_py() / bytes() handles the conversion.
    try:
        array_buffer  = await r2_object.arrayBuffer()
        original_bytes = bytes(array_buffer.to_py())
    except AttributeError:
        # Fallback for older Pyodide builds: use Uint8Array shim
        from js import Uint8Array  # noqa: PLC0415
        original_bytes = bytes(Uint8Array.new(await r2_object.arrayBuffer()))

    original_size = len(original_bytes)
    print(f"[INFO] Original size: {original_size:,} bytes")

    # --- Compress ---
    try:
        compressed_bytes = compress_pdf(original_bytes)
    except Exception as exc:
        print(f"[ERROR] Compression failed: {exc}")
        traceback.print_exc()
        return _json_response(
            {"error": f"PDF compression failed: {exc}"},
            status=422,
            extra_headers=cors_headers,
        )

    compressed_size = len(compressed_bytes)
    print(f"[INFO] Compressed size: {compressed_size:,} bytes "
          f"(ratio: {compressed_size / original_size:.1%})")

    # --- Build compressed file key: insert "-compressed" before extension ---
    compressed_key = _build_compressed_key(object_key)
    print(f"[INFO] Storing compressed file as: {compressed_key!r}")

    # --- Store compressed file back to R2 ---
    put_options = to_js({
        "httpMetadata": {"contentType": "application/pdf"},
    })
    await env.R2_BUCKET.put(compressed_key, to_js(compressed_bytes), put_options)

    # --- Generate presigned download URL ---
    try:
        expiry      = int(getattr(env, "PRESIGNED_URL_EXPIRY", _DEFAULT_EXPIRY)) * 60
        presigned   = generate_presigned_url(
            account_id        = env.R2_ACCOUNT_ID,
            access_key_id     = env.R2_ACCESS_KEY_ID,
            secret_access_key = env.R2_SECRET_ACCESS_KEY,
            bucket_name       = env.R2_BUCKET_NAME,
            object_key        = compressed_key,
            expires_in        = expiry,
        )
    except Exception as exc:
        print(f"[ERROR] Presigned URL generation failed: {exc}")
        traceback.print_exc()
        return _json_response(
            {"error": "Failed to generate presigned URL"},
            status=500,
            extra_headers=cors_headers,
        )

    print(f"[INFO] Presigned URL generated (expires in {expiry // 60} min)")

    return _json_response(
        {
            "success":        True,
            "compressedKey":  compressed_key,
            "presignedUrl":   presigned,
            "originalSize":   original_size,
            "compressedSize": compressed_size,
        },
        status=200,
        extra_headers=cors_headers,
    )


# ---------------------------------------------------------------------------
# Key-name helper
# ---------------------------------------------------------------------------

def _build_compressed_key(original_key: str) -> str:
    """Return the compressed variant of *original_key*.

    Inserts "-compressed" immediately before the file extension (if any).
    Examples:
      "report.pdf"           -> "report-compressed.pdf"
      "folder/scan.PDF"      -> "folder/scan-compressed.PDF"
      "no-extension"         -> "no-extension-compressed"
    """
    filename = original_key.split("/")[-1]
    if "." in filename:
        name, _, ext = original_key.rpartition(".")
        return f"{name}-compressed.{ext}"
    return f"{original_key}-compressed"


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def on_fetch(request, env, ctx):  # noqa: ARG001 (ctx unused but required by runtime)
    """Cloudflare Workers fetch handler — routes all incoming HTTP requests."""

    origin          = request.headers.get("Origin") or ""
    allowed_origins = getattr(env, "ALLOWED_ORIGINS", "*")
    cors_headers    = _build_cors_headers(origin, allowed_origins)

    print("[INFO] Handling /compress request:", request.headers.get("Origin"))

    # --- CORS preflight ---
    if request.method == "OPTIONS":
        return Response.new("", to_js({"status": 204, "headers": cors_headers}))

    # --- Route ---
    try:
        url_obj = urlparse(str(request.url))
        path    = url_obj.path.rstrip("/") or "/"

        print("[INFO] Incoming request:", request.method, path)

        if path == "/hello" and request.method == "GET":
            return _handle_hello()

        if path == "/admin/trigger-cleanup" and request.method == "POST":
            return await _handle_trigger_cleanup(request, env, cors_headers)

        if path == "/compress" and request.method == "POST":
            return await _handle_compress(request, env, cors_headers)

        return _json_response(
            {"error": "Not found"},
            status=404,
            extra_headers=cors_headers,
        )

    except Exception as exc:
        print(f"[ERROR] Unhandled exception: {exc}")
        traceback.print_exc()
        return _json_response(
            {"error": "Internal server error"},
            status=500,
            extra_headers=cors_headers,
        )


# ---------------------------------------------------------------------------
# Scheduled trigger handler
# ---------------------------------------------------------------------------

async def on_scheduled(event, env, ctx):  # noqa: ARG001
    """Cloudflare Workers cron trigger handler — cleans up old compressed files.

    Triggered according to the cron expression defined in wrangler.toml
    ([triggers] crons).  The age threshold for deletion is read from the
    CLEANUP_MINUTES environment variable at runtime.
    """
    cron = getattr(event, "cron", "unknown")
    print(f"[INFO] Scheduled cleanup triggered (cron: {cron})")

    try:
        result = await delete_old_compressed_files(env)
        deleted_count = len(result["deleted"])
        error_count   = len(result["errors"])
        print(
            f"[INFO] Scheduled cleanup complete: "
            f"{deleted_count} deleted, {result['skipped']} skipped, "
            f"{error_count} errors"
        )
        if error_count:
            print(f"[WARN] Keys that could not be deleted: {result['errors']}")
    except BaseException as exc:
        print(f"[ERROR] Scheduled cleanup failed: {exc}")
        traceback.print_exc()
