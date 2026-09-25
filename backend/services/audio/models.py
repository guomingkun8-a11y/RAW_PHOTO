from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


DOUBAO_TTS_MODEL = "doubao-tts-2.0"
GEM_TTS_MODEL = "gem-3.1-tts"
AUDIO_GENERATION_MODEL = DOUBAO_TTS_MODEL
AUDIO_GENERATION_MODELS = (DOUBAO_TTS_MODEL, GEM_TTS_MODEL)
AUDIO_GENERATION_MAX_CHARS = 100_000
DEFAULT_VOICE_ID = "zh_female_vv_uranus_bigtts"

AudioGenerationModelName = Literal["doubao-tts-2.0", "gem-3.1-tts"]
AudioSpeechRate = Literal[-50, -25, 0, 25, 50, 100]
AudioEmotion = Literal["auto", "happy", "sad", "angry", "fearful", "surprised", "calm"]
AudioFormat = Literal["mp3", "wav", "pcm", "ogg_opus"]


class AudioGenerationModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _normalized_voice_id(value: str) -> str:
    normalized = value.strip()
    if not normalized or any(character.isspace() for character in normalized):
        raise ValueError("voice_id 格式不正确")
    return normalized


class DoubaoAudioGenerationParams(AudioGenerationModel):
    voice_id: str = Field(default=DEFAULT_VOICE_ID, min_length=1, max_length=200)
    speech_rate: AudioSpeechRate = 0
    emotion: AudioEmotion = "auto"
    emotion_scale: int | None = Field(default=4, ge=1, le=5)
    format: AudioFormat = "mp3"

    @field_validator("voice_id")
    @classmethod
    def validate_voice_id(cls, value: str) -> str:
        return _normalized_voice_id(value)


class GemSpeakerVoiceConfig(AudioGenerationModel):
    speaker: str = Field(..., min_length=1, max_length=40)
    voice_id: str = Field(..., min_length=1, max_length=200)

    @field_validator("speaker")
    @classmethod
    def validate_speaker(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("角色名称不能为空")
        return normalized

    @field_validator("voice_id")
    @classmethod
    def validate_voice_id(cls, value: str) -> str:
        return _normalized_voice_id(value)


class GemAudioGenerationParams(AudioGenerationModel):
    voice_id: str | None = Field(default=None, min_length=1, max_length=200)
    speaker_voice_configs: list[GemSpeakerVoiceConfig] | None = Field(
        default=None,
        min_length=1,
        max_length=2,
    )

    @field_validator("voice_id")
    @classmethod
    def validate_voice_id(cls, value: str | None) -> str | None:
        return _normalized_voice_id(value) if value is not None else None

    @model_validator(mode="after")
    def validate_voice_mode(self):
        has_single_voice = self.voice_id is not None
        has_speaker_voices = bool(self.speaker_voice_configs)
        if has_single_voice == has_speaker_voices:
            raise ValueError("voice_id 与 speaker_voice_configs 必须且只能提供一个")
        if self.speaker_voice_configs:
            speaker_names = [item.speaker.casefold() for item in self.speaker_voice_configs]
            if len(speaker_names) != len(set(speaker_names)):
                raise ValueError("多角色配置中的角色名称不能重复")
        return self


AudioGenerationParams = DoubaoAudioGenerationParams | GemAudioGenerationParams


class AudioGenerationRequest(AudioGenerationModel):
    model: AudioGenerationModelName = AUDIO_GENERATION_MODEL
    prompt: str = Field(..., min_length=1, max_length=AUDIO_GENERATION_MAX_CHARS)
    params: AudioGenerationParams = Field(default_factory=DoubaoAudioGenerationParams)

    @model_validator(mode="before")
    @classmethod
    def validate_model_params(cls, value):
        if not isinstance(value, dict):
            return value
        model = str(value.get("model") or AUDIO_GENERATION_MODEL).strip()
        params_type = {
            DOUBAO_TTS_MODEL: DoubaoAudioGenerationParams,
            GEM_TTS_MODEL: GemAudioGenerationParams,
        }.get(model)
        if params_type is None:
            raise ValueError(f"不支持的音频模型: {model}")
        normalized = dict(value)
        normalized["model"] = model
        normalized["params"] = params_type.model_validate(value.get("params") or {})
        return normalized

    @model_validator(mode="after")
    def ensure_params_match_model(self):
        expected_type = (
            DoubaoAudioGenerationParams
            if self.model == DOUBAO_TTS_MODEL
            else GemAudioGenerationParams
        )
        if not isinstance(self.params, expected_type):
            raise ValueError("音频模型与参数不匹配")
        return self

    @field_validator("prompt")
    @classmethod
    def validate_prompt(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("请输入要生成的文本")
        return normalized
