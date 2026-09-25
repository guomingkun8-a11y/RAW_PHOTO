from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from services.audio.models import AUDIO_GENERATION_MODEL, AUDIO_GENERATION_MODELS, DEFAULT_VOICE_ID, DOUBAO_TTS_MODEL
from services.providers import openai_relay_service


class AudioGenerationService:
    @staticmethod
    def _payload_data(payload: dict[str, Any]) -> dict[str, Any]:
        nested = payload.get("data")
        return nested if isinstance(nested, dict) else payload

    def list_voices(self, model: str = AUDIO_GENERATION_MODEL) -> dict[str, object]:
        if model not in AUDIO_GENERATION_MODELS:
            raise ValueError(f"当前音频生成不支持 {model}")
        payload = openai_relay_service.list_media_voices(model)
        data = self._payload_data(payload)
        raw_voices = data.get("voices")
        if not isinstance(raw_voices, list):
            raise HTTPException(status_code=502, detail={"error": "音色列表返回格式不正确"})

        voices: list[dict[str, object]] = []
        seen: set[str] = set()
        for item in raw_voices:
            if not isinstance(item, dict):
                continue
            voice_id = str(item.get("voice_id") or item.get("id") or item.get("speaker") or "").strip()
            if not voice_id or voice_id in seen:
                continue
            seen.add(voice_id)
            voice: dict[str, object] = {
                "voice_id": voice_id,
                "name": str(item.get("name") or voice_id).strip() or voice_id,
            }
            for key in ("model", "type", "gender", "scene", "language", "description", "demo_audio"):
                value = item.get(key)
                if isinstance(value, (str, int, float, bool)) and str(value).strip():
                    voice[key] = value
            voices.append(voice)
        if not voices:
            raise HTTPException(status_code=502, detail={"error": "音色列表为空"})

        preferred_voice_id = DEFAULT_VOICE_ID if model == DOUBAO_TTS_MODEL else ""
        default_voice_id = preferred_voice_id if preferred_voice_id in seen else str(voices[0]["voice_id"])
        return {
            "model": model,
            "voices": voices,
            "total": len(voices),
            "default_voice_id": default_voice_id,
        }

audio_generation_service = AudioGenerationService()
