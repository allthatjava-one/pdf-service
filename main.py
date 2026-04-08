"""
FastAPI entry point for the PDF Compressor Service (Koyeb deployment).

Routes
------
  GET  /hello                  — Health check
  POST /compress               — Retrieve PDF from R2, compress, store back,
                                 return a presigned download URL
    POST /merge                  — Retrieve PDFs from R2, merge them, store back,
                                                                 return a presigned download URL
  POST /admin/trigger-cleanup  — Manually invoke the R2 cleanup (requires Bearer token)

Scheduled task
--------------
  Runs delete_old_compressed_files() on the interval defined by CLEANUP_MINUTES
  using APScheduler (started in the FastAPI lifespan).

Environment variables
---------------------
  R2_ACCOUNT_ID         — Cloudflare account ID
  R2_BUCKET_NAME        — R2 bucket name
  R2_ACCESS_KEY_ID      — R2 S3-compatible access key ID
  R2_SECRET_ACCESS_KEY  — R2 S3-compatible secret key
  ALLOWED_ORIGINS       — Comma-separated CORS origins, or "*"
  PRESIGNED_URL_EXPIRY  — Presigned URL lifetime in minutes (default: 60)
  CLEANUP_MINUTES       — Age threshold in minutes for cleanup (default: 60)
  ADMIN_SECRET          — Bearer token for /admin/* endpoints
  PORT                  — TCP port to listen on (injected by Koyeb, default: 8000)
"""

from __future__ import annotations

import io
import logging
import os
import traceback
import json
from contextlib import asynccontextmanager

import boto3
from dotenv import load_dotenv

load_dotenv()
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from src.cleanup import delete_old_compressed_files
from src.pdf_compressor import compress_pdf
from src.pdf_merger import merge_pdfs
from src.presigned_url import generate_presigned_url
from src.pdf_converter import convert_pdf
import asyncio

# limit concurrent heavy tasks (compress/merge/convert) to avoid memory spikes
_HEAVY_TASK_SEMAPHORE = asyncio.Semaphore(int(os.environ.get("MAX_CONCURRENT_HEAVY_TASKS", "1")))

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


_REQUIRED_ENVS = [
    "R2_ACCOUNT_ID",
    "R2_BUCKET_NAME",
    "R2_ACCESS_KEY_ID",
    "R2_SECRET_ACCESS_KEY",
]


def _check_required_envs() -> None:
    missing = [k for k in _REQUIRED_ENVS if not os.environ.get(k)]
    if missing:
        raise RuntimeError(
            f"Missing required environment variables: {', '.join(missing)}. "
            "Copy .env.example to .env and fill in the values."
        )


def _r2_client():
    _check_required_envs()
    return boto3.client(
        "s3",
        endpoint_url=f"https://{_env('R2_ACCOUNT_ID')}.r2.cloudflarestorage.com",
        aws_access_key_id=_env("R2_ACCESS_KEY_ID"),
        aws_secret_access_key=_env("R2_SECRET_ACCESS_KEY"),
        region_name="auto",
    )


def _build_compressed_key(original_key: str) -> str:
    if "." in original_key.split("/")[-1]:
        name, _, ext = original_key.rpartition(".")
        return f"{name}-compressed.{ext}"
    return f"{original_key}-compressed"


def _build_merged_key(original_key: str) -> str:
    if "." in original_key.split("/")[-1]:
        name, _, ext = original_key.rpartition(".")
        return f"{name}-merged.{ext}"
    return f"{original_key}-merged"


# ---------------------------------------------------------------------------
# Scheduler — runs cleanup periodically
# ---------------------------------------------------------------------------

scheduler = AsyncIOScheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    cleanup_minutes = int(_env("CLEANUP_MINUTES", "60"))
    scheduler.add_job(
        _scheduled_cleanup,
        "interval",
        minutes=cleanup_minutes,
        id="r2_cleanup",
    )
    scheduler.start()
    log.info("Scheduler started (cleanup every %d min)", cleanup_minutes)
    yield
    scheduler.shutdown()


async def _scheduled_cleanup():
    log.info("[CRON] Scheduled cleanup triggered")
    try:
        result = await delete_old_compressed_files(
            bucket_name=_env("R2_BUCKET_NAME"),
            s3_client=_r2_client(),
            cleanup_minutes=int(_env("CLEANUP_MINUTES", "60")),
        )
        log.info(
            "[CRON] Cleanup done: %d deleted, %d skipped, %d errors",
            len(result["deleted"]), result["skipped"], len(result["errors"]),
        )
    except Exception:
        log.error("[CRON] Cleanup failed:\n%s", traceback.format_exc())


# ---------------------------------------------------------------------------
# App & CORS
# ---------------------------------------------------------------------------

app = FastAPI(title="pdf-service", lifespan=lifespan)

_origins_cfg = _env("ALLOWED_ORIGINS", "*")
_origins = [o.strip() for o in _origins_cfg.split(",") if o.strip()] if _origins_cfg != "*" else ["*"]
# 
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
    max_age=86400,
)

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/hello")
def hello():
    return {"status": "ok", "message": "pdf-service is running"}


@app.post("/compress")
async def compress(request: Request):
    body = await request.json()
    object_key: str | None = body.get("objectKey") if isinstance(body, dict) else None
    if not object_key or not isinstance(object_key, str) or not object_key.strip():
        raise HTTPException(status_code=400, detail="Missing or invalid field: 'objectKey'")

    object_key = object_key.strip()
    log.info("[INFO] /compress request for key: %r", object_key)

    s3 = _r2_client()
    bucket = _env("R2_BUCKET_NAME")

    # Serialize heavy work to avoid concurrent memory spikes
    await _HEAVY_TASK_SEMAPHORE.acquire()
    log.info("[QUEUE] Acquired heavy task slot for /compress")
    try:
        # --- Fetch original file from R2 ---
        try:
            response = s3.get_object(Bucket=bucket, Key=object_key)
            original_bytes = response["Body"].read()
        except s3.exceptions.NoSuchKey:
            raise HTTPException(status_code=404, detail=f"Object not found: {object_key}")
        except Exception as exc:
            log.error("[ERROR] R2 fetch failed: %s", exc)
            raise HTTPException(status_code=500, detail="Failed to fetch object from R2")

        original_size = len(original_bytes)
        log.info("[INFO] Original size: %d bytes", original_size)

        # --- Compress ---
        try:
            compressed_bytes = compress_pdf(original_bytes)
        except Exception as exc:
            log.error("[ERROR] Compression failed: %s", exc)
            raise HTTPException(status_code=422, detail=f"PDF compression failed: {exc}")

        compressed_size = len(compressed_bytes)
        del original_bytes  # free source bytes — no longer needed
        log.info(
            "[INFO] Compressed size: %d bytes (ratio: %.1f%%)",
            compressed_size, compressed_size / original_size * 100,
        )

        # --- Store compressed file back to R2 ---
        compressed_key = _build_compressed_key(object_key)
        try:
            s3.put_object(
                Bucket=bucket,
                Key=compressed_key,
                Body=io.BytesIO(compressed_bytes),
                ContentType="application/pdf",
            )
        except Exception as exc:
            log.error("[ERROR] R2 put failed: %s", exc)
            raise HTTPException(status_code=500, detail="Failed to store compressed file in R2")

        log.info("[INFO] Stored compressed file as: %r", compressed_key)
    finally:
        _HEAVY_TASK_SEMAPHORE.release()
        log.info("[QUEUE] Released heavy task slot for /compress")

    # --- Generate presigned URL ---
    try:
        expiry_minutes = int(_env("PRESIGNED_URL_EXPIRY", "60"))
        presigned = generate_presigned_url(
            account_id=_env("R2_ACCOUNT_ID"),
            access_key_id=_env("R2_ACCESS_KEY_ID"),
            secret_access_key=_env("R2_SECRET_ACCESS_KEY"),
            bucket_name=bucket,
            object_key=compressed_key,
            expires_in=expiry_minutes * 60,
        )
    except Exception as exc:
        log.error("[ERROR] Presigned URL generation failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to generate presigned URL")

    log.info("[INFO] Presigned URL generated (expires in %d min)", expiry_minutes)

    return {
        "success": True,
        "compressedKey": compressed_key,
        "presignedUrl": presigned,
        "originalSize": original_size,
        "compressedSize": compressed_size,
    }


@app.post("/merge")
async def merge(request: Request):
    body = await request.json()
    object_keys = body.get("objectKeys") if isinstance(body, dict) else None
    compress = body.get("compress") if isinstance(body, dict) else False
    if not isinstance(object_keys, list) or len(object_keys) < 2:
        raise HTTPException(
            status_code=400,
            detail="Missing or invalid field: 'objectKeys' (must contain at least 2 items)",
        )

    normalized_keys = []
    for object_key in object_keys:
        if not isinstance(object_key, str) or not object_key.strip():
            raise HTTPException(status_code=400, detail="All 'objectKeys' entries must be non-empty strings")
        normalized_keys.append(object_key.strip())

    log.info("[INFO] /merge request for keys: %r, compress=%r", normalized_keys, compress)

    s3 = _r2_client()
    bucket = _env("R2_BUCKET_NAME")

    # Serialize heavy work to avoid concurrent memory spikes
    await _HEAVY_TASK_SEMAPHORE.acquire()
    log.info("[QUEUE] Acquired heavy task slot for /merge")
    try:
        source_pdfs: list[bytes] = []
        original_total_size = 0

        for object_key in normalized_keys:
            try:
                response = s3.get_object(Bucket=bucket, Key=object_key)
                pdf_bytes = response["Body"].read()
            except s3.exceptions.NoSuchKey:
                raise HTTPException(status_code=404, detail=f"Object not found: {object_key}")
            except Exception as exc:
                log.error("[ERROR] R2 fetch failed for %r: %s", object_key, exc)
                raise HTTPException(status_code=500, detail="Failed to fetch objects from R2")

            source_pdfs.append(pdf_bytes)
            original_total_size += len(pdf_bytes)

        try:
            merged_bytes = merge_pdfs(source_pdfs)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"PDF merge failed: {exc}")
        except Exception as exc:
            log.error("[ERROR] Merge failed: %s", exc)
            raise HTTPException(status_code=500, detail="Failed to merge PDF files")

        del source_pdfs  # free source PDF bytes — no longer needed

        merged_key = _build_merged_key(normalized_keys[0])
        result_key = merged_key
        merged_size = len(merged_bytes)
        compressed_size = None

        if compress:
            try:
                result_bytes = compress_pdf(merged_bytes)
                del merged_bytes  # free uncompressed merged PDF
                compressed_key = _build_compressed_key(merged_key)
                result_key = compressed_key
                compressed_size = len(result_bytes)
                log.info("[INFO] Compressed merged PDF: %d -> %d bytes", merged_size, compressed_size)
            except Exception as exc:
                log.error("[ERROR] Compression after merge failed: %s", exc)
                raise HTTPException(status_code=422, detail=f"Compression after merge failed: {exc}")
        else:
            result_bytes = merged_bytes

        try:
            s3.put_object(
                Bucket=bucket,
                Key=result_key,
                Body=io.BytesIO(result_bytes),
                ContentType="application/pdf",
            )
        except Exception as exc:
            log.error("[ERROR] R2 put failed for merged/compressed file: %s", exc)
            raise HTTPException(status_code=500, detail="Failed to store merged/compressed file in R2")
    finally:
        _HEAVY_TASK_SEMAPHORE.release()
        log.info("[QUEUE] Released heavy task slot for /merge")

    try:
        expiry_minutes = int(_env("PRESIGNED_URL_EXPIRY", "60"))
        presigned = generate_presigned_url(
            account_id=_env("R2_ACCOUNT_ID"),
            access_key_id=_env("R2_ACCESS_KEY_ID"),
            secret_access_key=_env("R2_SECRET_ACCESS_KEY"),
            bucket_name=bucket,
            object_key=result_key,
            expires_in=expiry_minutes * 60,
        )
    except Exception as exc:
        log.error("[ERROR] Presigned URL generation failed for merged/compressed file: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to generate presigned URL")

    response_data = {
        "success": True,
        "mergedKey": merged_key,
        "presignedUrl": presigned,
        "sourceCount": len(normalized_keys),
        "originalTotalSize": original_total_size,
        "mergedSize": merged_size,
        "resultKey": result_key,
    }
    if compress:
        response_data["compressedKey"] = result_key
        response_data["compressedSize"] = compressed_size

    return response_data


@app.post("/convert")
async def convert(request: Request):
    body = await request.json()
    object_key: str | None = body.get("objectKey") if isinstance(body, dict) else None
    convert_type: str | None = body.get("convertType") if isinstance(body, dict) else None
    languages = body.get("languages") if isinstance(body, dict) else None
    # Default to English when languages not provided
    if not languages:
        languages = ["en"]
    quality: str | None = body.get("quality") if isinstance(body, dict) else None

    if not object_key or not isinstance(object_key, str) or not object_key.strip():
        raise HTTPException(status_code=400, detail="Missing or invalid field: 'objectKey'")
    if not convert_type or not isinstance(convert_type, str):
        raise HTTPException(status_code=400, detail="Missing or invalid field: 'convertType'")

    object_key = object_key.strip()
    convert_type = convert_type.strip().lower()
    allowed = {"jpg", "png"}
    if convert_type not in allowed:
        raise HTTPException(status_code=400, detail=f"Unsupported convertType. Allowed: {sorted(allowed)}")

    log.info("[INFO] /convert request for key=%r type=%r languages=%r", object_key, convert_type, languages)

    s3 = _r2_client()
    bucket = _env("R2_BUCKET_NAME")

    # Serialize heavy work to avoid concurrent memory spikes
    await _HEAVY_TASK_SEMAPHORE.acquire()
    log.info("[QUEUE] Acquired heavy task slot for /convert")
    try:
        try:
            response = s3.get_object(Bucket=bucket, Key=object_key)
            original_bytes = response["Body"].read()
        except s3.exceptions.NoSuchKey:
            raise HTTPException(status_code=404, detail=f"Object not found: {object_key}")
        except Exception as exc:
            log.error("[ERROR] R2 fetch failed: %s", exc)
            raise HTTPException(status_code=500, detail="Failed to fetch object from R2")

        # Text (OCR) conversion has been removed to reduce application size.
        # Only page image exports (jpg/png as a ZIP) are supported.
        # convert_pdf is synchronous and CPU-intensive; run it in a thread pool so the
        # event loop stays responsive and Koyeb's reverse proxy does not time out.
        try:
            converted_bytes, content_type = await asyncio.to_thread(
                convert_pdf, original_bytes, convert_type, languages, None
            )
        except Exception as exc:
            log.error("[ERROR] Conversion failed: %s", exc)
            raise HTTPException(status_code=422, detail=f"PDF conversion failed: {exc}")

        ext = "zip"
    finally:
        _HEAVY_TASK_SEMAPHORE.release()
        log.info("[QUEUE] Released heavy task slot for /convert")
    # Normalize key to avoid repeated `.converted` suffixes.
    # If the original key already contains a `.converted` suffix, strip it first.
    import re

    base_key = re.sub(r"\.converted(?:\.[^.]+)?$", "", object_key)
    converted_key = f"{base_key}.converted.{ext}"

    try:
        s3.put_object(Bucket=bucket, Key=converted_key, Body=io.BytesIO(converted_bytes), ContentType=content_type)
    except Exception as exc:
        log.error("[ERROR] R2 put failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to store converted file in R2")

    try:
        expiry_minutes = int(_env("PRESIGNED_URL_EXPIRY", "60"))
        presigned = generate_presigned_url(
            account_id=_env("R2_ACCOUNT_ID"),
            access_key_id=_env("R2_ACCESS_KEY_ID"),
            secret_access_key=_env("R2_SECRET_ACCESS_KEY"),
            bucket_name=bucket,
            object_key=converted_key,
            expires_in=expiry_minutes * 60,
        )
    except Exception as exc:
        log.error("[ERROR] Presigned URL generation failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to generate presigned URL")

    return {
        "success": True,
        "presignedUrl": presigned,
        "originalKey": object_key,
        "convertedSize": len(converted_bytes),
    }


@app.post("/admin/trigger-cleanup")
async def trigger_cleanup(authorization: str = Header(default="")):
    admin_secret = _env("ADMIN_SECRET")
    if not admin_secret:
        raise HTTPException(status_code=500, detail="ADMIN_SECRET is not configured")
    if authorization != f"Bearer {admin_secret}":
        raise HTTPException(status_code=401, detail="Unauthorized")

    log.info("[INFO] Manual cleanup triggered via /admin/trigger-cleanup")
    try:
        result = await delete_old_compressed_files(
            bucket_name=_env("R2_BUCKET_NAME"),
            s3_client=_r2_client(),
            cleanup_minutes=int(_env("CLEANUP_MINUTES", "60")),
        )
        return {"success": True, "result": result}
    except Exception as exc:
        log.error("[ERROR] Cleanup failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Cleanup failed: {exc}")
