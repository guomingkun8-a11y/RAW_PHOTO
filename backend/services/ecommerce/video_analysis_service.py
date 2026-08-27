from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
import subprocess
import tempfile
from typing import Any, Mapping

from fastapi import HTTPException

from services.ecommerce.professional_video_service import professional_video_asset_service
from services.ecommerce.prompt_analysis_service import (
    is_prompt_analysis_enabled,
    prompt_analysis_model,
    request_audio_transcription,
    request_json_completion,
)
from services.platform.config import config


ANALYSIS_VERSION = 1


class VideoAnalysisError(RuntimeError):
    pass


@dataclass(frozen=True)
class ExtractedFrame:
    index: int
    time_sec: float
    path: Path
    data_url: str


def _clean(value: object, default: str = "", limit: int = 12000) -> str:
    text = str(value if value is not None else default).strip()
    return (text or default)[:limit]


def _number(value: object, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if number == number and number >= 0 else default


def _int(value: object, default: int = 0) -> int:
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return default
    return max(0, number)


def _rate(value: object) -> float:
    text = _clean(value)
    if not text or text == "0/0":
        return 0.0
    if "/" in text:
        left, right = text.split("/", 1)
        denominator = _number(right)
        return _number(left) / denominator if denominator else 0.0
    return _number(text)


def _run_command(command: list[str], *, timeout: int, label: str) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        executable = command[0] if command else label
        raise VideoAnalysisError(f"{label} is not available: {executable}") from exc
    except subprocess.TimeoutExpired as exc:
        raise VideoAnalysisError(f"{label} timed out") from exc
    if result.returncode != 0:
        error = (result.stderr or result.stdout or "").strip()[:1200]
        raise VideoAnalysisError(f"{label} failed: {error or f'exit code {result.returncode}'}")
    return result


def _probe_video(path: Path, settings: Mapping[str, object]) -> dict[str, Any]:
    result = _run_command(
        [
            _clean(settings.get("ffprobe_path"), "ffprobe"),
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ],
        timeout=60,
        label="ffprobe",
    )
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise VideoAnalysisError("ffprobe returned invalid JSON") from exc
    streams = payload.get("streams") if isinstance(payload.get("streams"), list) else []
    video_stream = next((item for item in streams if isinstance(item, dict) and item.get("codec_type") == "video"), {})
    audio_stream = next((item for item in streams if isinstance(item, dict) and item.get("codec_type") == "audio"), {})
    format_payload = payload.get("format") if isinstance(payload.get("format"), dict) else {}
    duration = _number(video_stream.get("duration") or format_payload.get("duration"))
    return {
        "durationSec": round(duration, 3),
        "width": _int(video_stream.get("width")),
        "height": _int(video_stream.get("height")),
        "fps": round(_rate(video_stream.get("avg_frame_rate") or video_stream.get("r_frame_rate")), 3),
        "videoCodec": _clean(video_stream.get("codec_name"), limit=80),
        "audioCodec": _clean(audio_stream.get("codec_name"), limit=80),
        "hasAudio": bool(audio_stream),
        "bitRate": _int(format_payload.get("bit_rate")),
    }


def _sample_times(duration_sec: float, settings: Mapping[str, object]) -> list[float]:
    max_frames = max(1, int(settings.get("max_frames") or 12))
    interval = max(1, int(settings.get("frame_interval_secs") or 8))
    max_duration = max(10, int(settings.get("max_duration_secs") or 300))
    effective = duration_sec if duration_sec > 0 else float(interval)
    effective = max(0.1, min(effective, float(max_duration)))
    by_interval = [float(second) for second in range(0, int(effective) + 1, interval)]
    if not by_interval:
        by_interval = [0.0]
    if len(by_interval) > max_frames:
        step = effective / max_frames
        by_interval = [step * index for index in range(max_frames)]
    bounded: list[float] = []
    last_allowed = max(0.0, (duration_sec if duration_sec > 0 else effective) - 0.1)
    for value in by_interval:
        normalized = round(min(max(0.0, value), last_allowed), 3)
        if normalized not in bounded:
            bounded.append(normalized)
    return bounded[:max_frames] or [0.0]


def _frame_data_url(path: Path) -> str:
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


def _extract_frames(source: Path, work_dir: Path, metadata: Mapping[str, Any], settings: Mapping[str, object]) -> list[ExtractedFrame]:
    times = _sample_times(_number(metadata.get("durationSec")), settings)
    frames: list[ExtractedFrame] = []
    width = max(160, int(settings.get("frame_width") or 768))
    for index, time_sec in enumerate(times, start=1):
        target = work_dir / f"frame-{index:03d}.jpg"
        try:
            _run_command(
                [
                    _clean(settings.get("ffmpeg_path"), "ffmpeg"),
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-ss",
                    f"{time_sec:.3f}",
                    "-i",
                    str(source),
                    "-frames:v",
                    "1",
                    "-vf",
                    f"scale={width}:-2:force_original_aspect_ratio=decrease",
                    "-q:v",
                    "3",
                    str(target),
                ],
                timeout=90,
                label="ffmpeg frame extraction",
            )
        except VideoAnalysisError:
            continue
        if target.exists() and target.stat().st_size > 0:
            frames.append(ExtractedFrame(index=index, time_sec=time_sec, path=target, data_url=_frame_data_url(target)))
    if not frames:
        raise VideoAnalysisError("ffmpeg could not extract any video frames")
    return frames


def _extract_audio(source: Path, target: Path, metadata: Mapping[str, Any], settings: Mapping[str, object]) -> bool:
    if not bool(metadata.get("hasAudio")):
        return False
    max_duration = max(10, int(settings.get("max_duration_secs") or 300))
    duration = _number(metadata.get("durationSec"), float(max_duration))
    effective_duration = max(0.1, min(duration or float(max_duration), float(max_duration)))
    _run_command(
        [
            _clean(settings.get("ffmpeg_path"), "ffmpeg"),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source),
            "-t",
            f"{effective_duration:.3f}",
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-b:a",
            "64k",
            str(target),
        ],
        timeout=180,
        label="ffmpeg audio extraction",
    )
    return target.exists() and target.stat().st_size > 0


def _transcribe_audio(audio_path: Path, settings: Mapping[str, object]) -> dict[str, str]:
    if not bool(settings.get("asr_enabled")):
        return {"status": "disabled", "text": "", "error": ""}
    max_bytes = max(1, int(settings.get("audio_max_mb") or 25)) * 1024 * 1024
    if audio_path.stat().st_size > max_bytes:
        return {"status": "skipped", "text": "", "error": "audio file exceeds configured ASR size limit"}
    try:
        text = request_audio_transcription(
            model=_clean(settings.get("asr_model"), "whisper-1", 160),
            file_path=audio_path,
            mime_type="audio/mpeg",
            timeout=180,
        )
    except Exception as exc:
        return {"status": "failed", "text": "", "error": _clean(exc, limit=1000)}
    return {"status": "ready" if text else "empty", "text": _clean(text, limit=12000), "error": ""}


def _list_strings(value: object, limit: int = 8) -> list[str]:
    if not isinstance(value, list):
        return []
    return [_clean(item, limit=500) for item in value if _clean(item, limit=500)][:limit]


def _dict_value(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _key_frames(value: object, limit: int) -> list[dict[str, Any]]:
    source = value if isinstance(value, list) else []
    items: list[dict[str, Any]] = []
    for item in source:
        if not isinstance(item, Mapping):
            continue
        items.append({
            "timeSec": round(_number(item.get("timeSec") or item.get("time_sec")), 3),
            "observation": _clean(item.get("observation") or item.get("description"), limit=1000),
            "product": _clean(item.get("product"), limit=500),
            "scene": _clean(item.get("scene"), limit=500),
            "text": _clean(item.get("text") or item.get("visibleText"), limit=500),
        })
        if len(items) >= limit:
            break
    return items


def _analyze_frames(
    frames: list[ExtractedFrame],
    metadata: Mapping[str, Any],
    transcript: Mapping[str, str],
    settings: Mapping[str, object],
) -> dict[str, Any]:
    if not is_prompt_analysis_enabled():
        raise VideoAnalysisError("openai relay is not enabled for video analysis")
    model = prompt_analysis_model(_clean(settings.get("vision_model"), "gpt-5.6-sol", 160))
    frame_index = [
        {"index": frame.index, "timeSec": frame.time_sec}
        for frame in frames
    ]
    content: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": json.dumps(
                {
                    "task": "Analyze sampled video frames and optional transcript for RAW ecommerce image Agent.",
                    "language": "Simplified Chinese",
                    "rules": [
                        "Use only visible frame evidence, transcript text, and media metadata.",
                        "Do not invent product claims, certifications, ingredients, measurements, discounts, rankings, or medical effects.",
                        "If evidence is uncertain, put the uncertainty in risks.",
                        "Focus on product identity, scene, usage, selling points that are actually visible or spoken, and image-generation directions.",
                    ],
                    "media": metadata,
                    "frames": frame_index,
                    "transcript": {
                        "status": transcript.get("status", ""),
                        "text": transcript.get("text", "")[:12000],
                    },
                    "jsonSchema": {
                        "summary": "string",
                        "productProfile": {"productName": "string", "category": "string", "visibleAttributes": ["string"], "uncertainties": ["string"]},
                        "sceneSummary": "string",
                        "keyFrames": [{"timeSec": "number", "observation": "string", "product": "string", "scene": "string", "text": "string"}],
                        "transcriptSummary": "string",
                        "sellingPoints": ["string"],
                        "visualDirections": ["string"],
                        "recommendedImagePrompts": ["string"],
                        "risks": ["string"],
                    },
                },
                ensure_ascii=False,
            ),
        }
    ]
    for frame in frames:
        content.append({
            "type": "image_url",
            "image_url": {"url": frame.data_url, "detail": "high"},
        })
    parsed = request_json_completion(
        model=model,
        system_prompt="You are a precise video analysis worker for RAW. Return strict JSON only.",
        content=content,
        max_tokens=3600,
        temperature=0.2,
    )
    return {
        "model": model,
        "summary": _clean(parsed.get("summary"), limit=2000),
        "productProfile": _dict_value(parsed.get("productProfile") or parsed.get("product_profile")),
        "sceneSummary": _clean(parsed.get("sceneSummary") or parsed.get("scene_summary"), limit=1500),
        "keyFrames": _key_frames(parsed.get("keyFrames") or parsed.get("key_frames"), len(frames)),
        "transcriptSummary": _clean(parsed.get("transcriptSummary") or parsed.get("transcript_summary"), limit=1500),
        "sellingPoints": _list_strings(parsed.get("sellingPoints") or parsed.get("selling_points")),
        "visualDirections": _list_strings(parsed.get("visualDirections") or parsed.get("visual_directions")),
        "recommendedImagePrompts": _list_strings(parsed.get("recommendedImagePrompts") or parsed.get("recommended_image_prompts"), 6),
        "risks": _list_strings(parsed.get("risks"), 10),
    }


class ProfessionalVideoAnalysisService:
    def analyze_video(self, video_id: str, *, owner_id: str) -> dict[str, Any]:
        current = professional_video_asset_service.get_video(video_id, owner_id=owner_id)
        if current is None:
            raise KeyError("video asset not found")
        if current.get("analysisStatus") == "ready" and isinstance(current.get("analysis"), dict):
            return dict(current["analysis"])

        settings = config.get_video_analysis_settings()
        if not bool(settings.get("enabled")):
            raise VideoAnalysisError("video analysis is disabled")

        professional_video_asset_service.mark_analysis_processing(video_id, owner_id=owner_id)
        with tempfile.TemporaryDirectory(prefix="raw-video-analysis-") as temp_dir:
            work_dir = Path(temp_dir)
            source_suffix = Path(_clean(current.get("name"), "video.mp4", 191)).suffix or ".mp4"
            source_path = work_dir / f"source-video{source_suffix}"
            asset = professional_video_asset_service.download_to_path(video_id, owner_id=owner_id, destination=source_path)
            metadata = _probe_video(source_path, settings)
            warnings: list[str] = []
            duration = _number(metadata.get("durationSec"))
            max_duration = max(10, int(settings.get("max_duration_secs") or 300))
            if duration > max_duration:
                warnings.append(f"视频时长 {duration:.1f}s 超过解析上限，本次只分析前 {max_duration}s。")

            frames = _extract_frames(source_path, work_dir, metadata, settings)
            transcript = {"status": "no_audio", "text": "", "error": ""}
            audio_path = work_dir / "audio.mp3"
            if bool(metadata.get("hasAudio")):
                try:
                    if _extract_audio(source_path, audio_path, metadata, settings):
                        transcript = _transcribe_audio(audio_path, settings)
                except VideoAnalysisError as exc:
                    transcript = {"status": "failed", "text": "", "error": _clean(exc, limit=1000)}

            vision = _analyze_frames(frames, metadata, transcript, settings)
            if transcript.get("error"):
                warnings.append(f"音频转写未完成：{transcript['error']}")

            analysis = {
                "version": ANALYSIS_VERSION,
                "status": "ready",
                "videoId": video_id,
                "name": asset.get("name") or asset.get("filename") or "",
                "url": asset.get("url") or "",
                "media": metadata,
                "sampling": {
                    "frameCount": len(frames),
                    "timesSec": [frame.time_sec for frame in frames],
                    "frameWidth": int(settings.get("frame_width") or 768),
                },
                "transcript": transcript,
                "model": vision.get("model"),
                "summary": vision.get("summary") or "",
                "productProfile": vision.get("productProfile") or {},
                "sceneSummary": vision.get("sceneSummary") or "",
                "keyFrames": vision.get("keyFrames") or [],
                "transcriptSummary": vision.get("transcriptSummary") or "",
                "sellingPoints": vision.get("sellingPoints") or [],
                "visualDirections": vision.get("visualDirections") or [],
                "recommendedImagePrompts": vision.get("recommendedImagePrompts") or [],
                "risks": [*warnings, *list(vision.get("risks") or [])],
                "createdAt": datetime.now().isoformat(timespec="seconds"),
            }
            professional_video_asset_service.mark_analysis_ready(video_id, owner_id=owner_id, analysis=analysis)
            return analysis

    def analyze_video_safely(self, video_id: str, *, owner_id: str) -> dict[str, Any]:
        try:
            return self.analyze_video(video_id, owner_id=owner_id)
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, str) else json.dumps(exc.detail, ensure_ascii=False)
            raise VideoAnalysisError(detail) from exc


professional_video_analysis_service = ProfessionalVideoAnalysisService()
