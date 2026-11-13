"""Uploads media to Cloudflare R2 so Instagram can fetch it from a public https URL."""
import mimetypes
from pathlib import Path

import boto3
from botocore.config import Config

from .config import S


def _client():
    if not (S.r2_account and S.r2_key and S.r2_secret and S.r2_public):
        raise RuntimeError("R2 settings missing in .env (R2_ACCOUNT_ID, keys, R2_PUBLIC_BASE_URL)")
    return boto3.client(
        "s3", endpoint_url=f"https://{S.r2_account}.r2.cloudflarestorage.com",
        aws_access_key_id=S.r2_key, aws_secret_access_key=S.r2_secret,
        region_name="auto", config=Config(signature_version="s3v4", retries={"max_attempts": 4}))


def upload(paths, prefix):
    """Returns the public URL of each file, in order."""
    c = _client()
    urls = []
    for p in map(Path, paths):
        key = f"{prefix.strip('/')}/{p.name}"
        ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        c.upload_file(str(p), S.r2_bucket, key, ExtraArgs={"ContentType": ctype})
        urls.append(f"{S.r2_public}/{key}")
    return urls


def check():
    c = _client()
    c.head_bucket(Bucket=S.r2_bucket)
    return f"bucket '{S.r2_bucket}' reachable, public base {S.r2_public}"
