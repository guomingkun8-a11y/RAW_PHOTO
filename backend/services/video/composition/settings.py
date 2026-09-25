from __future__ import annotations

from dataclasses import dataclass
import os
import shutil


def _bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _positive_int(name: str, default: int, minimum: int = 1) -> int:
    try:
        return max(minimum, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return max(minimum, default)


@dataclass(frozen=True)
class VideoCompositionSettings:
    enabled: bool
    queue_enabled: bool
    redis_url: str
    queue_name: str
    database_url: str
    ffmpeg_path: str
    ffprobe_path: str
    worker_concurrency: int
    max_upload_mb: int
    max_source_mb: int
    max_pending_per_owner: int
    tts_model: str = "doubao-tts-2.0"
    asr_model: str = "whisper-1"
    ai_timeout_secs: int = 300
    max_tts_chars: int = 5000
    max_retries: int = 1
    stale_running_timeout_secs: int = 7200

    @property
    def ffmpeg_available(self) -> bool:
        return bool(shutil.which(self.ffmpeg_path) or os.path.isfile(self.ffmpeg_path))

    @property
    def ffprobe_available(self) -> bool:
        return bool(shutil.which(self.ffprobe_path) or os.path.isfile(self.ffprobe_path))


def load_video_composition_settings() -> VideoCompositionSettings:
    return VideoCompositionSettings(
        enabled=_bool_env("VIDEO_COMPOSITION_ENABLED", True),
        queue_enabled=_bool_env("VIDEO_COMPOSITION_QUEUE_ENABLED", False),
        redis_url=(
            os.getenv("VIDEO_COMPOSITION_REDIS_URL")
            or os.getenv("VIDEO_GENERATION_REDIS_URL")
            or os.getenv("REDIS_URL")
            or "redis://127.0.0.1:6379/0"
        ).strip(),
        queue_name=os.getenv("VIDEO_COMPOSITION_QUEUE_NAME", "ai_video_composition_tasks").strip()
        or "ai_video_composition_tasks",
        database_url=os.getenv("VIDEO_COMPOSITION_DATABASE_URL", "").strip(),
        ffmpeg_path=os.getenv("VIDEO_COMPOSITION_FFMPEG_PATH", "ffmpeg").strip() or "ffmpeg",
        ffprobe_path=os.getenv("VIDEO_COMPOSITION_FFPROBE_PATH", "ffprobe").strip() or "ffprobe",
        worker_concurrency=_positive_int("VIDEO_COMPOSITION_WORKER_CONCURRENCY", 1),
        max_upload_mb=_positive_int("VIDEO_COMPOSITION_MAX_UPLOAD_MB", 250, 10),
        max_source_mb=_positive_int("VIDEO_COMPOSITION_MAX_SOURCE_MB", 1024, 64),
        max_pending_per_owner=_positive_int("VIDEO_COMPOSITION_OWNER_PENDING_LIMIT", 4),
        tts_model=os.getenv("VIDEO_COMPOSITION_TTS_MODEL", "doubao-tts-2.0").strip() or "doubao-tts-2.0",
        asr_model=os.getenv("VIDEO_COMPOSITION_ASR_MODEL", "whisper-1").strip() or "whisper-1",
        ai_timeout_secs=_positive_int("VIDEO_COMPOSITION_AI_TIMEOUT_SECS", 300, 30),
        max_tts_chars=_positive_int("VIDEO_COMPOSITION_MAX_TTS_CHARS", 5000, 100),
        max_retries=_positive_int("VIDEO_COMPOSITION_MAX_RETRIES", 1, 0),
        stale_running_timeout_secs=_positive_int("VIDEO_COMPOSITION_STALE_TIMEOUT_SECS", 7200, 300),
    )
