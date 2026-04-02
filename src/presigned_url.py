"""
Presigned URL generation for Cloudflare R2 via the S3-compatible API.

Implements AWS Signature Version 4 (SigV4) entirely with Python's standard
library (hmac, hashlib, urllib.parse, datetime) — no external dependencies.

R2 S3-compatible endpoint:
  https://<account_id>.r2.cloudflarestorage.com/<bucket>/<key>
"""

import hashlib
import hmac
import urllib.parse
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# Internal SigV4 helpers
# ---------------------------------------------------------------------------

def _sign(key: bytes, message: str) -> bytes:
    """HMAC-SHA256 of *message* using *key*."""
    return hmac.new(key, message.encode("utf-8"), hashlib.sha256).digest()


def _derive_signing_key(
    secret_key: str, date_stamp: str, region: str, service: str
) -> bytes:
    """Derive the SigV4 signing key via the four-step key derivation chain."""
    k_date    = _sign(("AWS4" + secret_key).encode("utf-8"), date_stamp)
    k_region  = _sign(k_date, region)
    k_service = _sign(k_region, service)
    k_signing = _sign(k_service, "aws4_request")
    return k_signing


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_presigned_url(
    account_id: str,
    access_key_id: str,
    secret_access_key: str,
    bucket_name: str,
    object_key: str,
    expires_in: int = 3600,
    region: str = "auto",
    method: str = "GET",
    custom_domain: str | None = None,
    custom_domain_is_bucket_root: bool = False,
) -> str:
    """Return a presigned URL that allows unauthenticated access to *object_key*.

    Args:
        account_id:        Cloudflare account ID.
        access_key_id:     R2 S3-compatible access key ID.
        secret_access_key: R2 S3-compatible secret access key.
        bucket_name:       R2 bucket name.
        object_key:        Object key (path inside the bucket).
        expires_in:        URL validity in seconds (max 604 800 — 7 days for R2).
        region:            Always "auto" for Cloudflare R2.
        method:            HTTP method the presigned URL will be valid for.

    Returns:
        A fully-formed presigned URL string.
    """
    service = "s3"
    # If a custom domain is provided (e.g. files.thrjtech.com), use it as the host
    host = custom_domain if custom_domain else f"{account_id}.r2.cloudflarestorage.com"

    now        = datetime.now(timezone.utc)
    date_stamp = now.strftime("%Y%m%d")           # e.g. "20260314"
    amz_date   = now.strftime("%Y%m%dT%H%M%SZ")  # e.g. "20260314T120000Z"

    credential_scope = f"{date_stamp}/{region}/{service}/aws4_request"
    credential       = f"{access_key_id}/{credential_scope}"

    # --- Canonical query string (parameters MUST be sorted lexicographically) ---
    query_params: dict[str, str] = {
        "X-Amz-Algorithm":     "AWS4-HMAC-SHA256",
        "X-Amz-Credential":    credential,
        "X-Amz-Date":          amz_date,
        "X-Amz-Expires":       str(expires_in),
        "X-Amz-SignedHeaders": "host",
    }
    canonical_querystring = "&".join(
        f"{urllib.parse.quote(k, safe='')}={urllib.parse.quote(v, safe='')}"
        for k, v in sorted(query_params.items())
    )

    # --- Canonical request ---
    encoded_key = urllib.parse.quote(object_key, safe="/")
    # If custom_domain points directly to the bucket root, omit the bucket
    # from the canonical URI (requests will be made to /<key> on that host).
    if custom_domain and custom_domain_is_bucket_root:
        canonical_uri = f"/{encoded_key}"
    else:
        canonical_uri = f"/{bucket_name}/{encoded_key}"
    canonical_headers  = f"host:{host}\n"
    signed_headers     = "host"

    canonical_request = "\n".join([
        method,
        canonical_uri,
        canonical_querystring,
        canonical_headers,
        signed_headers,
        "UNSIGNED-PAYLOAD",
    ])

    # --- String to sign ---
    canonical_request_hash = hashlib.sha256(
        canonical_request.encode("utf-8")
    ).hexdigest()

    string_to_sign = "\n".join([
        "AWS4-HMAC-SHA256",
        amz_date,
        credential_scope,
        canonical_request_hash,
    ])

    # --- Signature ---
    signing_key = _derive_signing_key(secret_access_key, date_stamp, region, service)
    signature   = hmac.new(
        signing_key, string_to_sign.encode("utf-8"), hashlib.sha256
    ).hexdigest()

    # --- Assemble final URL ---
    base_url = f"https://{host}{canonical_uri}"
    presigned_url = (
        f"{base_url}"
        f"?{canonical_querystring}"
        f"&X-Amz-Signature={signature}"
    )
    return presigned_url
