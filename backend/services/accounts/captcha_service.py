from __future__ import annotations

import base64
import io
import secrets
import string
import time
from dataclasses import dataclass

from captcha.image import ImageCaptcha


CAPTCHA_TTL_SECONDS = 300
CAPTCHA_CHARS = string.ascii_uppercase + string.digits


@dataclass
class CaptchaEntry:
    answer: str
    expires_at: float


class CaptchaService:
    def __init__(self) -> None:
        self._entries: dict[str, CaptchaEntry] = {}
        self._generator = ImageCaptcha(width=150, height=48)

    def _cleanup(self) -> None:
        now = time.time()
        expired = [captcha_id for captcha_id, entry in self._entries.items() if entry.expires_at <= now]
        for captcha_id in expired:
            self._entries.pop(captcha_id, None)

    def create(self) -> dict[str, str]:
        self._cleanup()
        answer = "".join(secrets.choice(CAPTCHA_CHARS) for _ in range(5))
        captcha_id = secrets.token_urlsafe(24)
        buffer = io.BytesIO()
        self._generator.write(answer, buffer, format="png")
        self._entries[captcha_id] = CaptchaEntry(answer=answer.lower(), expires_at=time.time() + CAPTCHA_TTL_SECONDS)
        image_data = base64.b64encode(buffer.getvalue()).decode("ascii")
        return {
            "captcha_id": captcha_id,
            "image_data_url": f"data:image/png;base64,{image_data}",
            "expires_in": str(CAPTCHA_TTL_SECONDS),
        }

    def verify(self, captcha_id: str, answer: str) -> bool:
        self._cleanup()
        normalized_id = str(captcha_id or "").strip()
        normalized_answer = str(answer or "").strip().lower()
        if not normalized_id or not normalized_answer:
            return False
        entry = self._entries.pop(normalized_id, None)
        if entry is None or entry.expires_at <= time.time():
            return False
        return secrets.compare_digest(entry.answer, normalized_answer)


captcha_service = CaptchaService()
