"""
Scheduled cleanup: deletes ALL files from R2 that are older than
CLEANUP_MINUTES minutes using the S3-compatible boto3 client.
"""

from __future__ import annotations

import logging
import time

log = logging.getLogger(__name__)

_DEFAULT_CLEANUP_MINUTES = 60


async def delete_old_compressed_files(
    bucket_name: str,
    s3_client,
    cleanup_minutes: int = _DEFAULT_CLEANUP_MINUTES,
) -> dict:
    cutoff_s = cleanup_minutes * 60
    now_s    = time.time()

    log.info("[INFO] cleanup: age threshold = %d minutes", cleanup_minutes)

    deleted: list[str] = []
    skipped = 0
    errors: list[str] = []

    paginator = s3_client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket_name):
        for obj in page.get("Contents", []):
            key: str = obj["Key"]
            age_s = now_s - obj["LastModified"].timestamp()

            if age_s < cutoff_s:
                skipped += 1
                continue

            try:
                s3_client.delete_object(Bucket=bucket_name, Key=key)
                deleted.append(key)
                log.info("[INFO] cleanup: deleted %r (age %.1f min)", key, age_s / 60)
            except Exception as exc:
                log.error("[ERROR] cleanup: failed to delete %r: %s", key, exc)
                errors.append(key)

    log.info(
        "[INFO] cleanup finished: %d deleted, %d skipped, %d errors",
        len(deleted), skipped, len(errors),
    )
    return {"deleted": deleted, "skipped": skipped, "errors": errors}
