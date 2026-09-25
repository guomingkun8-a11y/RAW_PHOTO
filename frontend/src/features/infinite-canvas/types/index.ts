import type { VideoGenerationDuration, VideoGenerationModelSpec } from '@/lib/api';
import type { CompositionTask, TimelineDocument } from '@/features/video-timeline/types/timeline';

export type NodeKind = 'text' | 'image' | 'video';
export type NodeStatus = 'idle' | 'uploading' | 'generating' | 'ready' | 'failed';
export type VideoAnalysisStatus = 'pending' | 'queued' | 'processing' | 'analyzing' | 'ready' | 'failed';

export const CANVAS_IMAGE_ASPECT_RATIOS = [
  '1:1',
  '2:3',
  '3:2',
  '3:4',
  '4:3',
  '4:5',
  '5:4',
  '9:16',
  '16:9',
  '21:9',
  '1:4',
  '4:1',
  '1:8',
  '8:1',
] as const;
export const CANVAS_IMAGE_SIZES = ['0.5K', '1K', '2K', '4K'] as const;
export const CANVAS_IMAGE_QUALITIES = ['auto', 'high', 'medium', 'low'] as const;
export const CANVAS_IMAGE_THINKING_LEVELS = ['minimal', 'high'] as const;
export const CANVAS_IMAGE_COUNTS = [1, 2, 3, 4] as const;

export interface ReferenceImage {
  id: string;
  name: string;
  url: string;
  previewUrl: string;
  uploadState: 'uploading' | 'ready' | 'failed';
}

export interface CanvasMediaReference {
  nodeId: string;
  type: 'image' | 'video';
  label: string;
  title: string;
  previewUrl?: string;
  imageIndex?: number;
  direct: boolean;
}

export interface CanvasVideoAnalysis {
  summary?: string;
  sceneSummary?: string;
  transcriptSummary?: string;
  visualDirections?: string[];
  sellingPoints?: string[];
  risks?: string[];
}

export interface CanvasNode {
  id: string;
  type: NodeKind;
  title: string;
  x: number;
  y: number;
  prompt: string;
  content: string;
  aspectRatio: string;
  resolution?: string;
  imageSize: string;
  quality: string;
  thinkingLevel: string;
  imageCount: number;
  duration: VideoGenerationDuration;
  references: ReferenceImage[];
  model?: string;
  taskId?: string;
  progress?: string;
  cost?: number;
  previewAspectRatio?: number;
  sourceDuration?: number;
  inputVideoId?: string;
  inputUrl?: string;
  videoAnalysisStatus?: VideoAnalysisStatus;
  videoAnalysisError?: string;
  videoAnalysis?: CanvasVideoAnalysis;
  resultUrl?: string;
  resultUrls?: string[];
  primaryImageIndex?: number;
  galleryExpanded?: boolean;
  status: NodeStatus;
  error?: string;
}

export interface CanvasConnection {
  id: string;
  from: string;
  to: string;
  sourceImageIndex?: number;
}

export interface WorkflowDocument {
  id: string;
  revision?: number;
  title: string;
  createdAt?: string;
  updatedAt?: string;
  coverUrl?: string;
  nodes: CanvasNode[];
  connections?: CanvasConnection[];
  viewport?: { x: number; y: number; zoom: number };
  assistantMessages?: ChatMessage[];
  timeline?: TimelineDocument;
  latestComposition?: CompositionTask;
}

export interface WorkflowSummary {
  id: string;
  title: string;
  nodeCount: number;
  coverUrl?: string;
  createdAt?: string;
  updatedAt?: string;
}

export interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
  timestamp?: string;
  media?: Array<{ type: string; url: string }>;
}

export interface ChatSession {
  id: string;
  topic?: string;
  createdAt?: string;
  updatedAt?: string;
  messageCount?: number;
  messages?: ChatMessage[];
}

export interface Capabilities {
  chat: boolean;
  image: boolean;
  video: boolean;
  referenceImageUpload: boolean;
  videoUpload: boolean;
}

export interface CanvasModelOptions {
  imageModel: string;
  imageModels: string[];
  videoModels: VideoGenerationModelSpec[];
  textVideoModel: string;
  imageVideoModel: string;
  textVideoQuality: string;
  imageVideoQuality: string;
}

export interface StoryboardScene {
  sceneNumber: number;
  description: string;
  cameraAngle: string;
  cameraMovement: string;
  lighting: string;
  mood: string;
}
