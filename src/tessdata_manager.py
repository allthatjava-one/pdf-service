import os
import pathlib
import io
import urllib.request
from typing import List
import boto3

def get_local_tessdata_dir() -> pathlib.Path:
    p = pathlib.Path.cwd() / "tessdata"
    p.mkdir(exist_ok=True)
    return p


def _download_from_github(lang: str, target: pathlib.Path, fast: bool = True) -> None:
    base = "https://github.com/tesseract-ocr/"
    repo = "tessdata_fast" if fast else "tessdata_best"
    url = f"{base}{repo}/raw/main/{lang}.traineddata"
    with urllib.request.urlopen(url, timeout=60) as resp:
        data = resp.read()
    target.write_bytes(data)


def _download_from_r2(lang: str, target: pathlib.Path, bucket: str, s3_client) -> None:
    key = f"{lang}.traineddata"
    resp = s3_client.get_object(Bucket=bucket, Key=key)
    data = resp["Body"].read()
    target.write_bytes(data)


def ensure_tessdata(langs: List[str] | None = None, quality: str = "low", r2_bucket: str | None = None, s3_client=None) -> pathlib.Path:
    """
    Ensure traineddata files exist locally. quality: 'low' -> download tessdata_fast from GitHub; 'high' -> download from R2 bucket (expects traineddata present) falling back to tessdata_best on GitHub.
    Returns local tessdata dir path and sets TESSDATA_PREFIX.
    """
    langs = langs or ["eng", "fra", "kor"]
    local = get_local_tessdata_dir()
    use_fast = quality != "high"

    for lang in langs:
        dst = local / f"{lang}.traineddata"
        if dst.exists() and dst.stat().st_size > 1000:
            continue
        # attempt sources
        # If quality is high, prefer R2 root key; if low, prefer R2 fast/ prefix
        tried = False
        if r2_bucket and s3_client:
            try:
                if quality == "high":
                    _download_from_r2(lang, dst, r2_bucket, s3_client)
                else:
                    # try fast/ prefix first
                    try:
                        key = f"fast/{lang}.traineddata"
                        resp = s3_client.get_object(Bucket=r2_bucket, Key=key)
                        dst.write_bytes(resp["Body"].read())
                    except Exception:
                        # fallback to root key
                        _download_from_r2(lang, dst, r2_bucket, s3_client)
                tried = True
            except Exception:
                tried = False
        if tried:
            continue
        # download from github
        try:
            _download_from_github(lang, dst, fast=use_fast)
        except Exception:
            # try best if fast failed
            _download_from_github(lang, dst, fast=False)

    # set environment variable
    os.environ["TESSDATA_PREFIX"] = str(local.resolve()) + os.sep
    return local
