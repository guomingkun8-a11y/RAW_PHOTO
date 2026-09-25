from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class TimelineModel(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class TimelineTransition(TimelineModel):
    type: Literal["cut", "fade"] = "cut"
    duration: float = Field(default=0.35, ge=0.05, le=2.0)


class TimelineVisualKeyframe(TimelineModel):
    scale: float = Field(default=1, ge=1, le=3)
    x: float = Field(default=0, ge=-1, le=1)
    y: float = Field(default=0, ge=-1, le=1)
    rotation: float = Field(default=0, ge=-180, le=180)
    brightness: float = Field(default=0, ge=-0.5, le=0.5)
    contrast: float = Field(default=1, ge=0.5, le=2)
    saturation: float = Field(default=1, ge=0, le=3)


class TimelineVisualKeyframes(TimelineModel):
    start: TimelineVisualKeyframe = Field(default_factory=TimelineVisualKeyframe)
    end: TimelineVisualKeyframe = Field(default_factory=TimelineVisualKeyframe)


class TimelineVideoClip(TimelineModel):
    id: str = Field(..., min_length=1, max_length=191)
    source_node_id: str = Field(default="", alias="sourceNodeId", max_length=191)
    title: str = Field(default="视频片段", max_length=191)
    source_url: str = Field(..., alias="sourceUrl", min_length=1, max_length=4000)
    source_start: float = Field(default=0, alias="sourceStart", ge=0, le=3600)
    source_duration: float | None = Field(default=None, alias="sourceDuration", gt=0, le=7200)
    duration: float = Field(default=5, gt=0, le=600)
    track: Literal[0, 1, 2] = 0
    timeline_start: float = Field(default=0, alias="timelineStart", ge=0, le=7200)
    opacity: float = Field(default=1, ge=0, le=1)
    volume: float = Field(default=1, ge=0, le=2)
    muted: bool = False
    transition: TimelineTransition = Field(default_factory=TimelineTransition)
    keyframes: TimelineVisualKeyframes = Field(default_factory=TimelineVisualKeyframes)

    @field_validator("source_url")
    @classmethod
    def validate_source_url(cls, value: str) -> str:
        source = value.strip()
        if not source.startswith(("http://", "https://", "/video-assets/", "/api/video-compositions/assets/")):
            raise ValueError("video source URL must be an HTTP(S) or managed local asset")
        return source


class TimelineAudioClip(TimelineModel):
    id: str = Field(..., min_length=1, max_length=191)
    kind: Literal["voiceover", "music"]
    title: str = Field(default="音频", max_length=191)
    source_url: str = Field(..., alias="sourceUrl", min_length=1, max_length=4000)
    storage_rel: str = Field(default="", alias="storageRel", max_length=1000)
    start: float = Field(default=0, ge=0, le=7200)
    source_start: float = Field(default=0, alias="sourceStart", ge=0, le=3600)
    duration: float = Field(default=5, gt=0, le=7200)
    volume: float = Field(default=1, ge=0, le=2)
    fade_in: float = Field(default=0, alias="fadeIn", ge=0, le=10)
    fade_out: float = Field(default=0, alias="fadeOut", ge=0, le=10)
    loop: bool = False
    script: str = Field(default="", max_length=20_000)

    @field_validator("source_url")
    @classmethod
    def validate_source_url(cls, value: str) -> str:
        source = value.strip()
        if not source.startswith(("http://", "https://", "/api/video-compositions/assets/")):
            raise ValueError("audio source URL must be an HTTP(S) or managed local asset")
        return source

    @model_validator(mode="after")
    def clamp_fades(self):
        self.fade_in = min(self.fade_in, self.duration)
        self.fade_out = min(self.fade_out, self.duration)
        return self


class TimelineSubtitleCue(TimelineModel):
    id: str = Field(..., min_length=1, max_length=191)
    start: float = Field(default=0, ge=0, le=7200)
    end: float = Field(..., gt=0, le=7200)
    text: str = Field(..., min_length=1, max_length=1000)
    source_audio_id: str = Field(default="", alias="sourceAudioId", max_length=191)

    @model_validator(mode="after")
    def validate_range(self):
        if self.end <= self.start:
            raise ValueError("subtitle end must be after start")
        return self


class TimelineOutputSettings(TimelineModel):
    aspect_ratio: Literal["16:9", "9:16", "1:1"] = Field(default="16:9", alias="aspectRatio")
    resolution: Literal["720p", "1080p"] = "1080p"
    fps: Literal[24, 25, 30] = 25
    burn_subtitles: bool = Field(default=True, alias="burnSubtitles")
    fit: Literal["contain", "cover"] = "contain"
    background_color: str = Field(default="#05070c", alias="backgroundColor", pattern=r"^#[0-9A-Fa-f]{6}$")

    def dimensions(self) -> tuple[int, int]:
        long_edge = 1920 if self.resolution == "1080p" else 1280
        short_edge = 1080 if self.resolution == "1080p" else 720
        if self.aspect_ratio == "9:16":
            return short_edge, long_edge
        if self.aspect_ratio == "1:1":
            return short_edge, short_edge
        return long_edge, short_edge


class TimelineDocument(TimelineModel):
    version: int = Field(default=1, ge=1, le=1)
    video_clips: list[TimelineVideoClip] = Field(default_factory=list, alias="videoClips", max_length=120)
    audio_clips: list[TimelineAudioClip] = Field(default_factory=list, alias="audioClips", max_length=80)
    subtitles: list[TimelineSubtitleCue] = Field(default_factory=list, max_length=1000)
    output: TimelineOutputSettings = Field(default_factory=TimelineOutputSettings)

    @model_validator(mode="after")
    def validate_video_clips(self):
        if not self.video_clips:
            raise ValueError("timeline requires at least one video clip")
        if not any(clip.track == 0 for clip in self.video_clips):
            raise ValueError("timeline requires at least one primary video clip")
        return self

    def duration(self) -> float:
        primary = [clip for clip in self.video_clips if clip.track == 0]
        total = sum(clip.duration for clip in primary)
        for index, clip in enumerate(primary):
            if index and clip.transition.type == "fade":
                total -= min(clip.transition.duration, clip.duration / 2, primary[index - 1].duration / 2)
        return max(0.05, total)


class VideoCompositionTaskRequest(TimelineModel):
    client_task_id: str = Field(..., min_length=1, max_length=191)
    workflow_id: str = Field(default="", max_length=191)
    timeline: TimelineDocument
    source: Literal["standard", "canvas"] = "standard"
    canvas_units: int = Field(default=1, ge=1, le=8)


class VoiceoverGenerationRequest(TimelineModel):
    text: str = Field(..., min_length=1, max_length=20_000)
    voice: str = Field(
        default="zh_female_vv_uranus_bigtts",
        min_length=1,
        max_length=160,
        pattern=r"^[A-Za-z0-9_.-]+$",
    )
    model: str = Field(default="", max_length=160)
    speech_rate: int = Field(default=0, alias="speechRate", ge=-50, le=100)
    emotion: Literal["auto", "happy", "sad", "angry", "surprised", "fear", "hate", "neutral", "chat"] = "auto"
    emotion_scale: int = Field(default=4, alias="emotionScale", ge=1, le=5)
    workflow_id: str = Field(default="", alias="workflowId", max_length=191)
    node_id: str = Field(default="", alias="nodeId", max_length=191)

    @model_validator(mode="before")
    @classmethod
    def map_legacy_speed(cls, value: object) -> object:
        if not isinstance(value, dict) or "speechRate" in value or "speech_rate" in value or "speed" not in value:
            return value
        try:
            multiplier = float(value.get("speed") or 1)
        except (TypeError, ValueError):
            return value
        normalized = dict(value)
        normalized["speechRate"] = max(-50, min(100, round((multiplier - 1) * 100)))
        return normalized


class AudioTranscriptionRequest(TimelineModel):
    storage_rel: str = Field(..., alias="storageRel", min_length=1, max_length=1000)
    language: str = Field(default="zh", max_length=20, pattern=r"^[A-Za-z-]*$")
    prompt: str = Field(default="", max_length=2000)
    offset: float = Field(default=0, ge=0, le=7200)
    workflow_id: str = Field(default="", alias="workflowId", max_length=191)
    node_id: str = Field(default="", alias="nodeId", max_length=191)
