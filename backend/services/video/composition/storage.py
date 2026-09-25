from __future__ import annotations

import hashlib
import mimetypes
import re
import subprocess
from array import array
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from urllib.parse import quote, unquote, urlparse

from fastapi import HTTPException
from services.platform.config import DATA_DIR
from services.video.video_generation_storage import video_generation_storage_service
from services.video.composition.settings import VideoCompositionSettings


COMPOSITION_DIR = DATA_DIR / "video_composition"
AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}


@dataclass(frozen=True)
class StoredCompositionAsset:
    relative_path: str
    url: str
    size: int
    duration: float
    waveform: tuple[float, ...] = ()
    metadata: dict[str, object] = field(default_factory=dict, repr=False, compare=False)


def _owner_scope(owner_id: str) -> str:
    return hashlib.sha256((owner_id or "anonymous").encode("utf-8")).hexdigest()[:24]


def _safe_name(value: str, default: str = "asset") -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())[:120] or default


def _safe_relative(value: str) -> str:
    normalized = value.replace("\\", "/").lstrip("/")
    parts = PurePosixPath(normalized).parts
    if not normalized or any(part in {"", ".", ".."} for part in parts):
        raise HTTPException(status_code=404, detail={"error": "composition asset not found"})
    return PurePosixPath(*parts).as_posix()


class VideoCompositionStorage:
    def __init__(self, settings: VideoCompositionSettings):
        self.settings = settings
        self.root = COMPOSITION_DIR

    def local_file(self, relative_path: str) -> Path:
        safe_relative = _safe_relative(relative_path)
        root = self.root.resolve()
        target = (root / safe_relative).resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail={"error": "composition asset not found"}) from exc
        if not target.is_file():
            raise HTTPException(status_code=404, detail={"error": "composition asset not found"})
        return target

    def probe_duration(self, path: Path) -> float:
        try:
            result = subprocess.run(
                [
                    self.settings.ffprobe_path,
                    "-v", "error",
                    "-show_entries", "format=duration",
                    "-of", "default=noprint_wrappers=1:nokey=1",
                    str(path),
                ],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=30,
            )
            return max(0.05, float(result.stdout.decode("utf-8", errors="replace").strip()))
        except (subprocess.SubprocessError, OSError, ValueError) as exc:
            raise ValueError("无法读取音频时长，请上传有效的音频文件") from exc

    def probe_waveform(self, path: Path, bars: int = 64) -> tuple[float, ...]:
        try:
            result = subprocess.run(
                [
                    self.settings.ffmpeg_path,
                    "-v", "error",
                    "-i", str(path),
                    "-vn", "-ac", "1", "-ar", "200",
                    "-f", "s16le", "pipe:1",
                ],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=60,
            )
        except (OSError, subprocess.SubprocessError):
            return ()
        samples = array("h")
        try:
            samples.frombytes(result.stdout)
        except (ValueError, EOFError):
            return ()
        if not samples:
            return ()
        bucket_size = max(1, len(samples) // max(1, bars))
        peaks = [
            max(abs(value) for value in samples[index:index + bucket_size]) / 32768
            for index in range(0, len(samples), bucket_size)
        ][:bars]
        maximum = max(peaks, default=0)
        if maximum <= 0:
            return tuple(0.08 for _ in peaks)
        return tuple(round(max(0.08, value / maximum), 3) for value in peaks)

    def save_audio(self, payload: bytes, filename: str, *, owner_id: str, base_url: str) -> StoredCompositionAsset:
        if not payload:
            raise ValueError("音频文件为空")
        if len(payload) > self.settings.max_upload_mb * 1024 * 1024:
            raise ValueError(f"音频文件不能超过 {self.settings.max_upload_mb} MB")
        suffix = Path(filename).suffix.lower()
        if suffix not in AUDIO_EXTENSIONS:
            raise ValueError("仅支持 MP3、WAV、M4A、AAC、OGG 和 FLAC 音频")
        digest = hashlib.sha256(payload).hexdigest()
        relative = f"uploads/{_owner_scope(owner_id)}/{digest[:24]}-{_safe_name(Path(filename).stem)}{suffix}"
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            target.write_bytes(payload)
        duration = self.probe_duration(target)
        waveform = self.probe_waveform(target)
        url = f"{base_url.rstrip('/')}/api/video-compositions/assets/{quote(relative, safe='/')}"
        return StoredCompositionAsset(relative, url, len(payload), duration, waveform)

    def owned_audio_file(self, relative_path: str, *, owner_id: str) -> Path:
        safe_relative = _safe_relative(relative_path)
        expected_prefix = f"uploads/{_owner_scope(owner_id)}/"
        if not safe_relative.startswith(expected_prefix):
            raise HTTPException(status_code=404, detail={"error": "audio asset not found"})
        return self.local_file(safe_relative)

    def delete_result(self, relative_path: str, *, owner_id: str) -> bool:
        value = str(relative_path or "").strip()
        if not value:
            return True
        safe_relative = _safe_relative(value)
        expected_prefix = f"results/{_owner_scope(owner_id)}/"
        if not safe_relative.startswith(expected_prefix):
            raise ValueError("合成文件不属于当前用户")
        target = (self.root.resolve() / safe_relative).resolve()
        try:
            target.relative_to(self.root.resolve())
        except ValueError as exc:
            raise ValueError("合成文件路径无效") from exc
        if not target.exists():
            return True
        try:
            target.unlink()
        except OSError as exc:
            raise ValueError(f"无法删除合成文件：{exc}") from exc
        return True

    @staticmethod
    def audio_mime_type(path: Path) -> str:
        return mimetypes.guess_type(path.name)[0] or "audio/mpeg"

    def result_target(self, *, owner_id: str, task_id: str, base_url: str) -> tuple[Path, str, str]:
        relative = f"results/{_owner_scope(owner_id)}/{_safe_name(task_id, 'composition')}.mp4"
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        url = f"{base_url.rstrip('/')}/api/video-compositions/assets/{quote(relative, safe='/')}"
        return target, relative, url

    def managed_source(self, source_url: str) -> Path | None:
        parsed = urlparse(source_url)
        path = unquote(parsed.path or source_url)
        composition_prefix = "/api/video-compositions/assets/"
        if path.startswith(composition_prefix):
            return self.local_file(path[len(composition_prefix):])
        video_prefix = "/video-assets/"
        if path.startswith(video_prefix):
            return video_generation_storage_service.local_file(path[len(video_prefix):])
        return None
