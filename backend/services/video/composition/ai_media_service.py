from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

from curl_cffi import requests
from fastapi import HTTPException

from services.ecommerce.prompt_analysis_service import (
    request_audio_transcription_result,
    response_cost,
    response_identifiers,
    response_usage,
)
from services.platform.config import config
from services.platform.proxy_service import proxy_settings
from services.providers.openai_relay_pool import (
    RelaySubmittedHTTPException,
    current_relay_account,
    run_with_relay_pool,
)
from services.video.composition.settings import VideoCompositionSettings
from services.video.composition.storage import StoredCompositionAsset, VideoCompositionStorage


logger = logging.getLogger(__name__)

DEFAULT_VOICE = "zh_female_vv_uranus_bigtts"
SUPPORTED_EMOTIONS = ("auto", "happy", "sad", "angry", "surprised", "fear", "hate", "neutral", "chat")
TTS_SPEECH_RATE_MIN = -50
TTS_SPEECH_RATE_MAX = 100
TTS_EMOTION_SCALE_MIN = 1
TTS_EMOTION_SCALE_MAX = 5
TTS_POLL_INTERVAL_SECS = 2.0
TTS_PENDING_STATES = {"pending", "queued", "waiting", "running", "processing", "in_progress"}
TTS_SUCCESS_STATES = {"success", "succeeded", "done", "complete", "completed"}
TTS_FAILURE_STATES = {"failed", "failure", "error", "canceled", "cancelled"}


def _relay_settings() -> dict[str, object]:
    return config.get_openai_relay_settings()


def _active_relay_settings() -> dict[str, object]:
    relay = _relay_settings()
    account = current_relay_account()
    if account is None:
        return relay
    active = dict(relay)
    active["base_url"] = account.base_url
    active["api_key"] = account.api_key
    return active


def _has_api_key(relay: dict[str, object]) -> bool:
    if str(relay.get("api_key") or "").strip():
        return True
    keys = relay.get("api_keys")
    if isinstance(keys, str):
        return bool(keys.strip())
    return isinstance(keys, (list, tuple, set)) and any(str(item or "").strip() for item in keys)


def _relay_url(path: str) -> str:
    relay = _active_relay_settings()
    base_url = str(relay.get("base_url") or "").strip().rstrip("/")
    if not base_url:
        raise HTTPException(status_code=500, detail={"error": "OpenAI Relay 地址未配置"})
    normalized = "/" + path.strip("/")
    if base_url.endswith("/v1") and normalized.startswith("/v1/"):
        normalized = normalized.removeprefix("/v1")
    return f"{base_url}{normalized}"


def _relay_headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    relay = _active_relay_settings()
    api_key = str(relay.get("api_key") or "").strip()
    if not api_key:
        raise HTTPException(status_code=500, detail={"error": "OpenAI Relay 密钥未配置"})
    return {"Authorization": f"Bearer {api_key}", **(extra or {})}


def _response_error(response: Any) -> HTTPException:
    try:
        detail = response.json()
    except Exception:
        detail = {"error": str(response.text or "")[:500] or f"HTTP {response.status_code}"}
    return HTTPException(status_code=int(response.status_code), detail=detail)


def _response_json_object(response: Any) -> dict[str, Any]:
    try:
        data = response.json()
    except Exception as exc:
        raise HTTPException(status_code=502, detail={"error": "语音接口返回的不是有效 JSON"}) from exc
    if not isinstance(data, dict):
        raise HTTPException(status_code=502, detail={"error": "语音接口返回格式不正确"})
    return data


def _media_payload(data: dict[str, Any]) -> dict[str, Any]:
    nested = data.get("data")
    return nested if isinstance(nested, dict) else data


def _media_task_id(data: dict[str, Any]) -> str:
    payload = _media_payload(data)
    candidates: list[object] = [
        data.get("task_id"),
        data.get("id"),
        payload.get("task_id"),
        payload.get("id"),
        payload.get("任务id"),
    ]
    task_ids = payload.get("任务ids")
    if isinstance(task_ids, list) and task_ids:
        candidates.append(task_ids[0])
    for candidate in candidates:
        task_id = str(candidate or "").strip()
        if task_id:
            return task_id
    raise HTTPException(status_code=502, detail={"error": "语音任务创建成功，但未返回 task_id"})


def _media_result_url(data: dict[str, Any]) -> str:
    direct_keys = ("result_url", "resultUrl", "output_url", "outputUrl", "audio_url", "audioUrl", "url")
    nested_keys = ("result", "output", "outputs", "artifacts", "data")

    def visit(value: object) -> str:
        if isinstance(value, str):
            candidate = value.strip()
            return candidate if candidate.startswith(("http://", "https://")) else ""
        if isinstance(value, list):
            for item in value:
                if result := visit(item):
                    return result
            return ""
        if not isinstance(value, dict):
            return ""
        for key in direct_keys:
            if result := visit(value.get(key)):
                return result
        for key in nested_keys:
            if result := visit(value.get(key)):
                return result
        return ""

    return visit(_media_payload(data))


def _media_error_text(data: dict[str, Any]) -> str:
    payload = _media_payload(data)
    for key in ("error", "message", "msg", "fail_reason", "failure_reason", "status"):
        value = payload.get(key)
        if value:
            if isinstance(value, (dict, list)):
                return json.dumps(value, ensure_ascii=False)[:500]
            return str(value)[:500]
    return json.dumps(data, ensure_ascii=False)[:500]


def _voice_options_once(*, model: str, timeout: int) -> list[dict[str, str]]:
    try:
        response = requests.get(
            _relay_url("/v1/skills/voices"),
            headers=_relay_headers({"Accept": "application/json"}),
            params={"model": model},
            timeout=min(60, max(1, timeout)),
            **proxy_settings.build_session_kwargs(),
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail={"error": f"读取豆包音色失败：{exc}"}) from exc
    if not 200 <= response.status_code < 300:
        raise _response_error(response)
    payload = _media_payload(_response_json_object(response))
    voices = payload.get("voices")
    if not isinstance(voices, list):
        raise HTTPException(status_code=502, detail={"error": "豆包音色列表返回格式不正确"})

    options: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in voices:
        if not isinstance(item, dict):
            continue
        voice_id = str(item.get("voice_id") or item.get("speaker") or item.get("id") or "").strip()
        if not voice_id or voice_id in seen:
            continue
        seen.add(voice_id)
        option = {
            "id": voice_id,
            "name": str(item.get("name") or voice_id).strip() or voice_id,
        }
        for key in ("gender", "scene", "language", "description", "demo_audio"):
            value = str(item.get(key) or "").strip()
            if value:
                option[key] = value
        options.append(option)
    if not options:
        raise HTTPException(status_code=502, detail={"error": "豆包音色列表为空"})
    return options


def _poll_tts_task_details(task_id: str, *, deadline: float) -> dict[str, Any]:
    last_status: dict[str, Any] = {}
    while time.monotonic() <= deadline:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            response = requests.get(
                _relay_url("/v1/media/status"),
                headers=_relay_headers({"Accept": "application/json"}),
                params={"task_id": task_id},
                timeout=min(30, max(1, remaining)),
                **proxy_settings.build_session_kwargs(),
            )
        except Exception as exc:
            raise HTTPException(status_code=502, detail={"error": f"查询语音任务失败：{exc}"}) from exc
        if not 200 <= response.status_code < 300:
            raise _response_error(response)
        last_status = _response_json_object(response)
        payload = _media_payload(last_status)
        state = str(payload.get("state") or "").strip().casefold()
        result_url = _media_result_url(last_status)
        is_final = payload.get("is_final") is True

        if state in TTS_FAILURE_STATES:
            raise HTTPException(status_code=502, detail={"error": f"豆包配音生成失败：{_media_error_text(last_status)}"})
        if result_url and (is_final or state in TTS_SUCCESS_STATES or not state):
            return {"result_url": result_url, "response": last_status}
        if is_final or state in TTS_SUCCESS_STATES:
            raise HTTPException(status_code=502, detail={"error": "豆包配音任务已结束，但未返回音频地址"})
        if state and state not in TTS_PENDING_STATES:
            logger.debug("Unknown Doubao TTS state %s for task %s", state, task_id)

        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(min(TTS_POLL_INTERVAL_SECS, remaining))
    detail = _media_error_text(last_status) if last_status else "尚未返回最终状态"
    raise HTTPException(status_code=504, detail={"error": f"豆包配音生成超时：{detail}"})


def _poll_tts_task(task_id: str, *, deadline: float) -> str:
    return str(_poll_tts_task_details(task_id, deadline=deadline).get("result_url") or "")


def _doubao_tts_details_once(
    *,
    text: str,
    voice: str,
    model: str,
    speech_rate: int,
    emotion: str,
    emotion_scale: int,
    timeout: int,
) -> dict[str, Any]:
    deadline = time.monotonic() + max(1, timeout)
    task_id = ""
    try:
        response = requests.post(
            _relay_url("/v1/media/generate"),
            headers=_relay_headers({"Accept": "application/json", "Content-Type": "application/json"}),
            json={
                "model": model,
                "prompt": text,
                "params": {
                    "speaker": voice,
                    "speech_rate": speech_rate,
                    "emotion": emotion,
                    "emotion_scale": emotion_scale,
                    "format": "mp3",
                },
            },
            timeout=min(60, max(1, timeout)),
            **proxy_settings.build_session_kwargs(),
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail={"error": f"提交豆包配音任务失败：{exc}"}) from exc
    if not 200 <= response.status_code < 300:
        raise _response_error(response)
    submission = _response_json_object(response)
    task_id = _media_task_id(submission)

    try:
        polled = _poll_tts_task_details(task_id, deadline=deadline)
        result_url = str(polled.get("result_url") or "")
        status_response = polled.get("response") if isinstance(polled.get("response"), dict) else {}
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise HTTPException(status_code=504, detail={"error": "豆包配音已生成，但下载音频超时"})
        try:
            audio_response = requests.get(
                result_url,
                headers={"Accept": "audio/mpeg,audio/*;q=0.9,*/*;q=0.1"},
                timeout=min(180, max(1, remaining)),
                allow_redirects=True,
                **proxy_settings.build_session_kwargs(),
            )
        except Exception as exc:
            raise HTTPException(status_code=502, detail={"error": f"下载豆包配音失败：{exc}"}) from exc
        if not 200 <= audio_response.status_code < 300:
            raise _response_error(audio_response)
        payload = bytes(audio_response.content)
        if not payload:
            raise HTTPException(status_code=502, detail={"error": "豆包配音下载结果为空"})
        content_type = str(audio_response.headers.get("content-type") or "audio/mpeg").split(";", 1)[0]
        submission_cost = response_cost(submission)
        return {
            "payload": payload,
            "content_type": content_type,
            "cost": submission_cost if submission_cost is not None else response_cost(status_response),
            "upstream_ids": list(dict.fromkeys([task_id, *response_identifiers(submission), *response_identifiers(status_response)])),
            "usage": response_usage(submission) or response_usage(status_response),
        }
    except RelaySubmittedHTTPException:
        raise
    except HTTPException as exc:
        raise RelaySubmittedHTTPException(
            status_code=exc.status_code,
            detail=exc.detail,
            upstream_task_ids=[task_id],
        ) from exc


def _doubao_tts_once(
    *,
    text: str,
    voice: str,
    model: str,
    speech_rate: int,
    emotion: str,
    emotion_scale: int,
    timeout: int,
) -> tuple[bytes, str]:
    details = _doubao_tts_details_once(
        text=text,
        voice=voice,
        model=model,
        speech_rate=speech_rate,
        emotion=emotion,
        emotion_scale=emotion_scale,
        timeout=timeout,
    )
    return bytes(details.get("payload") or b""), str(details.get("content_type") or "audio/mpeg")


def _split_caption_text(text: str, max_chars: int = 24) -> list[str]:
    paragraphs = [item.strip() for item in re.split(r"(?<=[。！？!?；;])\s*|\n+", text) if item.strip()]
    chunks: list[str] = []
    for paragraph in paragraphs:
        remaining = paragraph
        while len(remaining) > max_chars:
            split_at = max(1, max(remaining.rfind(mark, 0, max_chars + 1) for mark in ("，", ",", "、", " ")))
            if split_at <= 1:
                split_at = max_chars
            chunks.append(remaining[:split_at].strip())
            remaining = remaining[split_at:].lstrip("，,、 ")
        if remaining:
            chunks.append(remaining)
    return chunks or ([text.strip()] if text.strip() else [])


def _caption_cues(data: dict[str, Any], *, duration: float, offset: float) -> list[dict[str, object]]:
    cues: list[dict[str, object]] = []
    segments = data.get("segments")
    if isinstance(segments, list):
        for index, segment in enumerate(segments):
            if not isinstance(segment, dict):
                continue
            text = str(segment.get("text") or "").strip()
            try:
                start = max(0.0, float(segment.get("start") or 0))
                end = min(duration, float(segment.get("end") or duration))
            except (TypeError, ValueError):
                continue
            if text and end > start:
                cues.append({
                    "id": f"subtitle-asr-{index + 1}",
                    "start": round(offset + start, 3),
                    "end": round(offset + end, 3),
                    "text": text,
                })
    if cues:
        return cues

    chunks = _split_caption_text(str(data.get("text") or "").strip())
    if not chunks:
        return []
    weights = [max(1, len(chunk)) for chunk in chunks]
    total_weight = sum(weights)
    cursor = 0.0
    for index, (chunk, weight) in enumerate(zip(chunks, weights)):
        start = cursor
        cursor = duration if index == len(chunks) - 1 else cursor + duration * weight / total_weight
        cues.append({
            "id": f"subtitle-asr-{index + 1}",
            "start": round(offset + start, 3),
            "end": round(offset + max(start + 0.35, cursor), 3),
            "text": chunk,
        })
    return cues


class VideoCompositionAIMediaService:
    def __init__(self, settings: VideoCompositionSettings, storage: VideoCompositionStorage):
        self.settings = settings
        self.storage = storage

    def capabilities(self) -> dict[str, object]:
        relay = _relay_settings()
        configured = bool(relay.get("enabled") and relay.get("base_url") and _has_api_key(relay))
        voice_options = [{"id": DEFAULT_VOICE, "name": "Vivi 温柔女声"}]
        if configured:
            try:
                voice_options = run_with_relay_pool(
                    relay,
                    "video_composition_tts_voices",
                    lambda: _voice_options_once(
                        model=self.settings.tts_model,
                        timeout=self.settings.ai_timeout_secs,
                    ),
                )
            except Exception as exc:
                logger.warning("Unable to load Doubao TTS voices; using the documented default: %s", exc)
        voice_ids = [str(item["id"]) for item in voice_options]
        default_voice = DEFAULT_VOICE if DEFAULT_VOICE in voice_ids else voice_ids[0]
        return {
            "voiceover": {
                "enabled": configured,
                "model": self.settings.tts_model,
                "voices": voice_ids,
                "voice_options": voice_options,
                "default_voice": default_voice,
                "speech_rate": {"min": TTS_SPEECH_RATE_MIN, "max": TTS_SPEECH_RATE_MAX, "default": 0},
                "emotions": list(SUPPORTED_EMOTIONS),
                "emotion_scale": {
                    "min": TTS_EMOTION_SCALE_MIN,
                    "max": TTS_EMOTION_SCALE_MAX,
                    "default": 4,
                },
                "max_chars": self.settings.max_tts_chars,
            },
            "transcription": {
                "enabled": configured,
                "model": self.settings.asr_model,
                "timestamps": True,
            },
            "music_generation": {
                "enabled": False,
                "reason": "当前 Relay 未提供可验证的音乐生成模型，背景音乐支持上传和自动混音。",
            },
        }

    def generate_voiceover(
        self,
        *,
        text: str,
        voice: str,
        model: str,
        speech_rate: int,
        emotion: str,
        emotion_scale: int,
        owner_id: str,
        base_url: str,
    ) -> StoredCompositionAsset:
        script = str(text or "").strip()
        if not script:
            raise ValueError("请输入旁白文案")
        if len(script) > self.settings.max_tts_chars:
            raise ValueError(f"单次旁白不能超过 {self.settings.max_tts_chars} 个字符")
        selected_voice = str(voice or DEFAULT_VOICE).strip()
        if not selected_voice:
            raise ValueError("请选择配音音色")
        requested_model = str(model or "").strip()
        selected_model = self.settings.tts_model
        if requested_model and requested_model != selected_model:
            raise ValueError(f"当前配音仅支持 {selected_model}")
        selected_emotion = str(emotion or "auto").strip().lower()
        if selected_emotion not in SUPPORTED_EMOTIONS:
            raise ValueError("不支持的配音情绪")
        normalized_speech_rate = max(TTS_SPEECH_RATE_MIN, min(TTS_SPEECH_RATE_MAX, int(speech_rate)))
        normalized_emotion_scale = max(
            TTS_EMOTION_SCALE_MIN,
            min(TTS_EMOTION_SCALE_MAX, int(emotion_scale)),
        )
        details = run_with_relay_pool(
            _relay_settings(),
            "video_composition_tts",
            lambda: _doubao_tts_details_once(
                text=script,
                voice=selected_voice,
                model=selected_model,
                speech_rate=normalized_speech_rate,
                emotion=selected_emotion,
                emotion_scale=normalized_emotion_scale,
                timeout=self.settings.ai_timeout_secs,
            ),
        )
        asset = self.storage.save_audio(
            bytes(details.get("payload") or b""),
            f"voiceover-{selected_voice}.mp3",
            owner_id=owner_id,
            base_url=base_url,
        )
        return StoredCompositionAsset(
            relative_path=asset.relative_path,
            url=asset.url,
            size=asset.size,
            duration=asset.duration,
            waveform=asset.waveform,
            metadata={
                "cost": details.get("cost"),
                "upstream_ids": details.get("upstream_ids") or [],
                "usage": details.get("usage"),
            },
        )

    def transcribe(
        self,
        *,
        storage_rel: str,
        owner_id: str,
        language: str,
        prompt: str,
        offset: float,
    ) -> dict[str, object]:
        source = self.storage.owned_audio_file(storage_rel, owner_id=owner_id)
        duration = self.storage.probe_duration(source)
        details = request_audio_transcription_result(
            model=self.settings.asr_model,
            file_path=source,
            prompt=str(prompt or "").strip(),
            mime_type=self.storage.audio_mime_type(source),
            timeout=self.settings.ai_timeout_secs,
            language=str(language or "").strip(),
            timestamps=True,
            operation="audio_transcription",
        )
        data = details.get("data") if isinstance(details.get("data"), dict) else {}
        cues = _caption_cues(data, duration=duration, offset=max(0.0, float(offset)))
        return {
            "text": str(data.get("text") or "").strip() or "\n".join(str(cue["text"]) for cue in cues),
            "duration": duration,
            "model": self.settings.asr_model,
            "cues": cues,
            "_call_metadata": {
                "cost": details.get("cost"),
                "upstream_ids": details.get("upstream_ids") or [],
                "usage": details.get("usage"),
            },
        }
