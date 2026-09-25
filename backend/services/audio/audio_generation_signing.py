"""Short-lived, read-only capabilities for generated local audio files."""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import time

from services.platform.config import config


MAX_URL_TTL_SECS = 3600
_DOMAIN = b"gmkraw:audio-generation:read:v1"


def result_url_ttl() -> int:
    try:
        value = int(os.getenv("AUDIO_GENERATION_URL_TTL_SECS", "900"))
    except ValueError:
        value = 900
    return max(60, min(MAX_URL_TTL_SECS, value))


def _signing_key() -> bytes:
    secret = os.getenv("AUDIO_GENERATION_SIGNING_SECRET", "").strip() or str(config.auth_key or "").strip()
    if not secret:
        raise ValueError("audio result signing secret is not configured")
    # Domain separation keeps audio capabilities independent of authentication.
    return hmac.new(secret.encode("utf-8"), _DOMAIN, hashlib.sha256).digest()


def sign_audio_path(relative_path: str, expires: int) -> str:
    payload = f"{relative_path}\n{expires}".encode("utf-8")
    return hmac.new(_signing_key(), payload, hashlib.sha256).hexdigest()


def verify_audio_signature(relative_path: str, expires: object, signature: str) -> bool:
    value = str(expires)
    if not re.fullmatch(r"[0-9]{1,12}", value) or not re.fullmatch(r"[0-9a-f]{64}", signature):
        return False
    expiry = int(value)
    now = int(time.time())
    if expiry <= now or expiry > now + MAX_URL_TTL_SECS:
        return False
    return hmac.compare_digest(sign_audio_path(relative_path, expiry), signature)
