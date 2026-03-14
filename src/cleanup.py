"""
Scheduled cleanup: deletes ALL files from R2 that are older than
CLEANUP_MINUTES minutes.

Called by the `on_scheduled` Cloudflare Workers cron trigger.  The age
threshold is controlled entirely by the CLEANUP_MINUTES environment variable
so you can tune retention without redeploying.
"""

from __future__ import annotations

import time

from pyodide.ffi import to_js

_DEFAULT_CLEANUP_MINUTES = 60


async def delete_old_compressed_files(env) -> dict:
    """Delete ALL objects in R2 that are older than CLEANUP_MINUTES.

    Paginates through the entire bucket and removes every object whose
    uploaded timestamp exceeds the configured age threshold.

    Args:
        env: Cloudflare Worker environment (provides R2_BUCKET binding and vars).

    Returns:
        A dict with keys ``deleted``, ``skipped``, and ``errors``.
    """
    cleanup_minutes = int(getattr(env, "CLEANUP_MINUTES", _DEFAULT_CLEANUP_MINUTES))
    cutoff_ms = cleanup_minutes * 60 * 1000

    print(f"[INFO] cleanup: age threshold = {cleanup_minutes} minutes")

    # Use Python stdlib time — avoids relying on js.Date.
    now_ms = time.time() * 1000

    deleted: list[str] = []
    skipped = 0
    errors: list[str] = []

    cursor = None

    while True:
        if cursor:
            result = await env.R2_BUCKET.list(to_js({"cursor": cursor}))
        else:
            result = await env.R2_BUCKET.list()

        # Iterate the JS Array safely: index by position using .length.
        num_objects = int(result.objects.length)
        print(f"[INFO] cleanup: listing page with {num_objects} object(s)")

        for i in range(num_objects):
            obj = result.objects[i]
            key: str = str(obj.key)

            try:
                # obj.uploaded is a JS Date; valueOf() returns ms since epoch.
                uploaded_ms = float(obj.uploaded.valueOf())
                age_ms = now_ms - uploaded_ms
            except Exception as exc:
                print(f"[WARN] cleanup: could not read upload time for {key!r}: {exc}")
                errors.append(key)
                continue

            if age_ms < cutoff_ms:
                skipped += 1
                print(
                    f"[DEBUG] cleanup: keeping {key!r} "
                    f"(age {age_ms / 60_000:.1f} min)"
                )
                continue

            try:
                await env.R2_BUCKET.delete(key)
                deleted.append(key)
                print(
                    f"[INFO] cleanup: deleted {key!r} "
                    f"(age {age_ms / 60_000:.1f} min)"
                )
            except Exception as exc:
                print(f"[ERROR] cleanup: failed to delete {key!r}: {exc}")
                errors.append(key)

        # Pagination — result.truncated is a JS boolean.
        if not result.truncated:
            break
        cursor = str(result.cursor)

    print(
        f"[INFO] cleanup finished: {len(deleted)} deleted, "
        f"{skipped} skipped, {len(errors)} errors"
    )

    return {
        "deleted": deleted,
        "skipped": skipped,
        "errors":  errors,
    }

