import base64
import hashlib
import os
import uuid

import boto3
from botocore.client import Config
from fastapi import HTTPException

MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024  # 10MB decoded size cap, matches the old Mongo-era limit

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = boto3.client(
            "s3",
            endpoint_url=os.environ["S3_ENDPOINT_URL"],
            aws_access_key_id=os.environ["S3_ACCESS_KEY_ID"],
            aws_secret_access_key=os.environ["S3_SECRET_ACCESS_KEY"],
            config=Config(signature_version="s3v4"),
            region_name=os.environ.get("S3_REGION", "us-east-1"),
        )
    return _client


def _bucket() -> str:
    return os.environ["S3_BUCKET_NAME"]


def classify_attachment_type(mime_type: str) -> str:
    """Ported unchanged from the Mongo-era classify_attachment_type."""
    mime_type = (mime_type or "").lower()
    if mime_type.startswith("image/"):
        return "image"
    if mime_type == "application/pdf":
        return "pdf"
    if "word" in mime_type or mime_type == "application/msword":
        return "word"
    if "excel" in mime_type or "spreadsheet" in mime_type:
        return "excel"
    if "powerpoint" in mime_type or "presentation" in mime_type:
        return "powerpoint"
    if "zip" in mime_type or "compressed" in mime_type:
        return "zip"
    if mime_type.startswith("audio/"):
        return "audio"
    if mime_type.startswith("video/"):
        return "video"
    return "other"


def decode_and_validate(data: str, filename: str, mime_type: str = None) -> bytes:
    if not filename or not data:
        raise HTTPException(status_code=400, detail="Each attachment requires a filename and data")
    # mime_type is optional so callers with their own tighter allow-list
    # (profile photo, employee CV) aren't double-validated here - generic
    # task/message/calendar attachments pass it through and are rejected
    # if classify_attachment_type can't place them in any known category
    # (e.g. executables, raw HTML/scripts).
    if mime_type is not None and classify_attachment_type(mime_type) == "other":
        raise HTTPException(status_code=400, detail="This file type is not supported")
    raw = data.split(",", 1)[-1] if data.startswith("data:") else data
    try:
        decoded = base64.b64decode(raw, validate=False)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid file data")
    if len(decoded) > MAX_ATTACHMENT_BYTES:
        raise HTTPException(status_code=400, detail=f"File exceeds the {MAX_ATTACHMENT_BYTES // (1024*1024)}MB limit")
    return decoded


def upload(decoded: bytes, *, prefix: str) -> tuple:
    """Uploads to object storage, returns (storage_path, checksum)."""
    checksum = hashlib.sha256(decoded).hexdigest()
    storage_path = f"{prefix}/{uuid.uuid4().hex}"
    _get_client().put_object(Bucket=_bucket(), Key=storage_path, Body=decoded)
    return storage_path, checksum


def download_base64(storage_path: str) -> str:
    """Re-fetches the file and re-encodes it inline, so the response
    contract (base64 'data' field) stays byte-identical to the old
    Mongo-era implementation that stored the base64 directly - see the
    migration plan's decision on attachment response-shape preservation."""
    obj = _get_client().get_object(Bucket=_bucket(), Key=storage_path)
    return base64.b64encode(obj["Body"].read()).decode()


def delete(storage_path: str) -> None:
    _get_client().delete_object(Bucket=_bucket(), Key=storage_path)
