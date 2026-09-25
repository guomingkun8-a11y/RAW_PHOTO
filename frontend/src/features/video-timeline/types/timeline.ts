export type TimelineTransitionType = 'cut' | 'fade';

export interface TimelineTransition {
  type: TimelineTransitionType;
  duration: number;
}

export interface TimelineVisualKeyframe {
  scale: number;
  x: number;
  y: number;
  rotation: number;
  brightness: number;
  contrast: number;
  saturation: number;
}

export interface TimelineVisualKeyframes {
  start: TimelineVisualKeyframe;
  end: TimelineVisualKeyframe;
}

export interface TimelineVideoClip {
  id: string;
  sourceNodeId?: string;
  title: string;
  sourceUrl: string;
  thumbnailUrl?: string;
  sourceStart: number;
  sourceDuration?: number;
  duration: number;
  track: 0 | 1 | 2;
  timelineStart: number;
  opacity: number;
  volume: number;
  muted: boolean;
  transition: TimelineTransition;
  keyframes: TimelineVisualKeyframes;
}

export type TimelineAudioKind = 'voiceover' | 'music';

export interface TimelineAudioClip {
  id: string;
  kind: TimelineAudioKind;
  title: string;
  sourceUrl: string;
  storageRel?: string;
  start: number;
  sourceStart: number;
  duration: number;
  volume: number;
  fadeIn: number;
  fadeOut: number;
  loop: boolean;
  script?: string;
  waveform?: number[];
}

export interface TimelineSubtitleCue {
  id: string;
  start: number;
  end: number;
  text: string;
  sourceAudioId?: string;
}

export type TimelineAspectRatio = '16:9' | '9:16' | '1:1';
export type TimelineResolution = '720p' | '1080p';

export interface TimelineOutputSettings {
  aspectRatio: TimelineAspectRatio;
  resolution: TimelineResolution;
  fps: 24 | 25 | 30;
  burnSubtitles: boolean;
  fit: 'contain' | 'cover';
  backgroundColor: string;
}

export interface TimelineDocument {
  version: 1;
  videoClips: TimelineVideoClip[];
  audioClips: TimelineAudioClip[];
  subtitles: TimelineSubtitleCue[];
  output: TimelineOutputSettings;
}

export interface TimelineAudioAsset {
  id: string;
  title: string;
  url: string;
  storageRel: string;
  duration: number;
  size: number;
  waveform?: number[];
  script?: string;
  voice?: string;
}

export type TimelineVoiceEmotion =
  | 'auto'
  | 'happy'
  | 'sad'
  | 'angry'
  | 'surprised'
  | 'fear'
  | 'hate'
  | 'neutral'
  | 'chat';

export interface TimelineVoiceOption {
  id: string;
  name: string;
  gender?: string;
  scene?: string;
  language?: string;
  description?: string;
  demo_audio?: string;
}

export interface TimelineVoiceoverInput {
  text: string;
  voice: string;
  speechRate: number;
  emotion: TimelineVoiceEmotion;
  emotionScale: number;
  createCaptions: boolean;
}

export interface TimelineCapabilities {
  voiceover: {
    enabled: boolean;
    model: string;
    voices: string[];
    voice_options?: TimelineVoiceOption[];
    default_voice?: string;
    speech_rate?: { min: number; max: number; default: number };
    emotions?: TimelineVoiceEmotion[];
    emotion_scale?: { min: number; max: number; default: number };
    max_chars: number;
  };
  transcription: {
    enabled: boolean;
    model: string;
    timestamps: boolean;
  };
  music_generation: {
    enabled: boolean;
    reason?: string;
  };
}

export interface TimelineTranscription {
  text: string;
  duration: number;
  model: string;
  cues: TimelineSubtitleCue[];
}

export type CompositionTaskStatus = 'queued' | 'running' | 'success' | 'error' | 'canceled';

export interface CompositionTask {
  id: string;
  workflow_id?: string;
  status: CompositionTaskStatus;
  progress?: string;
  result_url?: string;
  resultUrl?: string;
  duration?: number;
  width?: number;
  height?: number;
  size?: number;
  error?: string;
  created_at?: string;
  finished_at?: string;
}
