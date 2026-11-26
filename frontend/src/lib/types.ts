export interface JobOptions {
  clip_count: number;
  min_clip_sec: number;
  max_clip_sec: number;
  output_format: "9:16" | "1:1" | "16:9";
  crop_mode: "center" | "smart" | "blur_fit";
  caption_style: "bold_yellow" | "clean" | "minimal";
  captions_enabled: boolean;
  quality: "720p" | "1080p";
  whisper_model?: string;
  language?: string | null;
  denoise_audio: boolean;
}

export interface JobLogEntry {
  time: string;
  stage: string;
  message: string;
}

export interface ClipScores {
  final: number;
  llm: number | null;
  replay_heat: number | null;
  audio_energy: number | null;
  hook: number;
  pace: number;
}

export interface ClipMetadata {
  youtube_shorts?: {
    title?: string;
    description?: string;
    hashtags?: string[];
  };
  instagram_reels?: {
    caption?: string;
    hashtags?: string[];
  };
  tiktok?: {
    caption?: string;
  };
  on_screen_hook?: string;
  alternative_titles?: string[];
}

export interface Clip {
  id: string;
  job_id: string;
  rank: number;
  start_sec: number;
  end_sec: number;
  duration_sec: number;
  final_score: number;
  scores: ClipScores;
  llm_reason: string;
  hook_quality: string;
  standalone: boolean;
  transcript_text: string;
  output_format: "9:16" | "1:1" | "16:9";
  crop_mode: "center" | "smart" | "blur_fit";
  caption_style: "bold_yellow" | "clean" | "minimal";
  captions_enabled: boolean;
  denoise_audio: boolean;
  render_version: number;
  metadata: ClipMetadata;
  created_at: string;
  updated_at: string;
}

export interface Job {
  id: string;
  url: string;
  video_id: string;
  video_title: string;
  channel: string;
  duration: number;
  thumbnail_url: string;
  status: "awaiting_upload" | "queued" | "running" | "completed" | "failed";
  stage: string;
  progress: number;
  status_message: string;
  error_message: string | null;
  options: Partial<JobOptions>;
  logs: JobLogEntry[];
  clip_count: number;
  clips?: Clip[];
  created_at: string;
  updated_at: string;
}

export interface SettingsResponse {
  whisper_model: string;
  default_format: "9:16" | "1:1" | "16:9";
  default_caption_style: "bold_yellow" | "clean" | "minimal";
  auto_delete_days: number;
  gemini_key_configured: boolean;
  gemini_model: string;
  storage_path: string;
  scoring_weights: Record<string, number>;
  caption_presets: Record<string, string>;
  dependencies: {
    ok: boolean;
    ffmpeg: string | null;
    yt_dlp: string | null;
    hw_encoder: string;
  };
  disk_usage: {
    downloads_bytes: number;
    work_bytes: number;
    outputs_bytes: number;
    total_bytes: number;
  };
}

export interface SSEEvent {
  type: "snapshot" | "progress" | "completed" | "failed";
  status: Job["status"];
  stage: string;
  progress: number;
  message: string;
  error?: string | null;
  log?: JobLogEntry;
  speed_bps?: number | null;
  transferred_bytes?: number | null;
  total_bytes?: number | null;
  transfer_mode?: "download" | "upload" | null;
}
