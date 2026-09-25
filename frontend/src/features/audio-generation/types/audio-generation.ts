export const DOUBAO_TTS_MODEL = "doubao-tts-2.0" as const;
export const GEM_TTS_MODEL = "gem-3.1-tts" as const;
export const AUDIO_GENERATION_MODEL = DOUBAO_TTS_MODEL;
export const AUDIO_GENERATION_MODELS = [DOUBAO_TTS_MODEL, GEM_TTS_MODEL] as const;
export const DEFAULT_AUDIO_VOICE_ID = "zh_female_vv_uranus_bigtts";
export const AUDIO_PROMPT_MAX_CHARS = 100_000;

export type AudioGenerationModel = typeof AUDIO_GENERATION_MODELS[number];
export type AudioSpeechRate = -50 | -25 | 0 | 25 | 50 | 100;
export type AudioEmotion = "auto" | "happy" | "sad" | "angry" | "fearful" | "surprised" | "calm";
export type AudioOutputFormat = "mp3" | "wav" | "pcm" | "ogg_opus";
export type GemReadingMode = "single" | "dialogue";

export type AudioVoice = {
  voice_id: string;
  name: string;
  model?: string;
  type?: string;
  gender?: string;
  scene?: string;
  language?: string;
  description?: string;
  demo_audio?: string;
};

export type AudioVoiceListResponse = {
  model: AudioGenerationModel;
  voices: AudioVoice[];
  total: number;
  default_voice_id: string;
};

export type DoubaoAudioGenerationParams = {
  voice_id: string;
  speech_rate: AudioSpeechRate;
  emotion: AudioEmotion;
  emotion_scale?: number;
  format: AudioOutputFormat;
};

export type GemSpeakerVoiceConfig = {
  speaker: string;
  voice_id: string;
};

export type GemAudioGenerationParams = {
  voice_id?: string;
  speaker_voice_configs?: GemSpeakerVoiceConfig[];
};

export type AudioGenerationParams = DoubaoAudioGenerationParams | GemAudioGenerationParams;

export type AudioGenerationRequest =
  | {
    model: typeof DOUBAO_TTS_MODEL;
    prompt: string;
    params: DoubaoAudioGenerationParams;
  }
  | {
    model: typeof GEM_TTS_MODEL;
    prompt: string;
    params: GemAudioGenerationParams;
  };

export type AudioGenerationStatus = "queued" | "running" | "success" | "error" | "canceled";

export type AudioGenerationTask = {
  id: string;
  owner_id?: string;
  owner_name?: string;
  owner_username?: string;
  status: AudioGenerationStatus;
  mode: "single_voice" | "dialogue" | string;
  model: AudioGenerationModel;
  prompt: string;
  params: AudioGenerationParams;
  progress?: string | number;
  audio_url?: string;
  data?: Array<{ type?: string; url: string }>;
  error?: string;
  cost?: number | null;
  upstream_task_id?: string;
  duration_ms?: number;
  elapsed_secs?: number;
  voice_id?: string;
  output_format?: string;
  storage?: "remote" | "local" | "oss" | string;
  file_size?: number;
  storage_error?: string;
  cancellation_pending?: boolean;
  reconciliation_required?: boolean;
  created_at: string;
  updated_at: string;
};

export type AudioGenerationTaskRequest = AudioGenerationRequest & {
  client_task_id: string;
  conversation_id?: string;
  turn_id?: string;
};

export type AudioGenerationTaskListResponse = {
  items: AudioGenerationTask[];
  missing_ids: string[];
  has_more?: boolean;
  limit?: number;
  total?: number;
  next_cursor?: string | null;
};
