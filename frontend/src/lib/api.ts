import type {
  Clip,
  ClipMetadata,
  Job,
  JobOptions,
  PublishClipResult,
  PublishStatus,
  SettingsResponse,
} from "./types";

export const API_BASE =
  process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, "") || "http://localhost:8000";

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      "ngrok-skip-browser-warning": "true",
      ...(init?.headers || {}),
    },
  });
  if (!res.ok) {
    let detail = `Request failed (${res.status})`;
    try {
      const body = await res.json();
      if (body?.detail) detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      // ignore non-JSON body
    }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

export function createJob(url: string, options: JobOptions): Promise<Job> {
  return apiFetch<Job>("/api/jobs", {
    method: "POST",
    body: JSON.stringify({ url, options }),
  });
}

export function createUploadJob(
  file: File,
  options: JobOptions,
  title: string,
  onProgress?: (pct: number) => void
): Promise<Job> {
  return new Promise((resolve, reject) => {
    const form = new FormData();
    form.append("file", file);
    form.append("options", JSON.stringify(options));
    form.append("title", title);
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${API_BASE}/api/jobs/upload`);
    xhr.setRequestHeader("ngrok-skip-browser-warning", "true");
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && onProgress) onProgress(Math.round((e.loaded / e.total) * 100));
    };
    xhr.onload = () => {
      try {
        const body = JSON.parse(xhr.responseText || "{}");
        if (xhr.status >= 200 && xhr.status < 300) resolve(body as Job);
        else reject(new Error(body?.detail || `Upload failed (${xhr.status})`));
      } catch {
        reject(new Error(`Upload failed (${xhr.status})`));
      }
    };
    xhr.onerror = () => reject(new Error("Upload failed — could not reach the backend"));
    xhr.send(form);
  });
}

export function listJobs(): Promise<Job[]> {
  return apiFetch<Job[]>("/api/jobs");
}

export function getJob(jobId: string): Promise<Job> {
  return apiFetch<Job>(`/api/jobs/${encodeURIComponent(jobId)}`);
}

export function retryJob(jobId: string): Promise<Job> {
  return apiFetch<Job>(`/api/jobs/${encodeURIComponent(jobId)}/retry`, {
    method: "POST",
  });
}

export function deleteJob(jobId: string): Promise<{ deleted: boolean }> {
  return apiFetch<{ deleted: boolean }>(`/api/jobs/${encodeURIComponent(jobId)}`, {
    method: "DELETE",
  });
}

export function jobEventsUrl(jobId: string): string {
  return `${API_BASE}/api/jobs/${encodeURIComponent(jobId)}/events`;
}

export function clipDownloadUrl(
  clipId: string,
  type: "mp4" | "mp4_nocaptions" | "mp3" | "srt" | "thumb" | "meta",
  inline = false,
  version?: number
): string {
  const params = new URLSearchParams({ type });
  if (inline) params.set("inline", "true");
  if (version !== undefined) params.set("v", String(version));
  return `${API_BASE}/api/clips/${encodeURIComponent(clipId)}/download?${params.toString()}`;
}

export async function downloadJobZip(jobId: string, clipIds: string[]): Promise<void> {
  const res = await fetch(`${API_BASE}/api/jobs/${encodeURIComponent(jobId)}/zip`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "ngrok-skip-browser-warning": "true",
    },
    body: JSON.stringify({ clip_ids: clipIds }),
  });
  if (!res.ok) {
    let msg = `ZIP download failed (${res.status})`;
    try {
      const body = await res.json();
      if (body?.detail) msg = body.detail;
    } catch {
      // ignore
    }
    throw new Error(msg);
  }
  const blob = await res.blob();
  const disp = res.headers.get("Content-Disposition") || "";
  const match = disp.match(/filename="([^"]+)"/);
  const filename = match ? match[1] : `clipforge_${jobId}.zip`;
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

export function rerenderClip(
  clipId: string,
  payload: {
    output_format?: "9:16" | "1:1" | "16:9";
    crop_mode?: "center" | "smart" | "blur_fit";
    caption_style?: "bold_yellow" | "clean" | "minimal";
    captions_enabled?: boolean;
    start_sec?: number;
    end_sec?: number;
    denoise_audio?: boolean;
  }
): Promise<Clip> {
  return apiFetch<Clip>(`/api/clips/${encodeURIComponent(clipId)}/rerender`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function updateClipMetadata(clipId: string, metadata: ClipMetadata): Promise<Clip> {
  return apiFetch<Clip>(`/api/clips/${encodeURIComponent(clipId)}/metadata`, {
    method: "PATCH",
    body: JSON.stringify(metadata),
  });
}

export function getSettings(): Promise<SettingsResponse> {
  return apiFetch<SettingsResponse>("/api/settings");
}

export function updateSettings(payload: {
  whisper_model?: string;
  default_format?: string;
  default_caption_style?: string;
  auto_delete_days?: number;
}): Promise<SettingsResponse> {
  return apiFetch<SettingsResponse>("/api/settings", {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export function cleanupStorage(payload: {
  older_than_days?: number;
  clear_cache?: boolean;
}): Promise<{ removed_jobs: number; freed_bytes: number; remaining_bytes: number }> {
  return apiFetch("/api/storage/cleanup", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function getPublishStatus(): Promise<PublishStatus> {
  return apiFetch<PublishStatus>("/api/publish/status");
}

export function youtubeConnectUrl(): string {
  const origin = typeof window !== "undefined" ? window.location.origin : "";
  const qs = origin ? `?return_to=${encodeURIComponent(origin)}` : "";
  return `${API_BASE}/api/auth/youtube/start${qs}`;
}

export function disconnectYoutube(): Promise<{ disconnected: boolean }> {
  return apiFetch("/api/auth/youtube/disconnect", { method: "POST" });
}

export function saveInstagramConfig(
  access_token: string,
  ig_user_id: string
): Promise<{ connected: boolean; ig_user_id: string }> {
  return apiFetch("/api/publish/instagram/config", {
    method: "POST",
    body: JSON.stringify({ access_token, ig_user_id }),
  });
}

export function publishClips(payload: {
  clip_ids: string[];
  platforms: ("youtube" | "instagram")[];
  privacy_status: "private" | "unlisted" | "public";
  use_nocaptions: boolean;
}): Promise<{ results: PublishClipResult[] }> {
  return apiFetch("/api/publish", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function formatDuration(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const rem = s % 60;
  if (h > 0) return `${h}:${String(m).padStart(2, "0")}:${String(rem).padStart(2, "0")}`;
  return `${m}:${String(rem).padStart(2, "0")}`;
}

export function formatBytes(bytes: number): string {
  if (bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const idx = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)));
  const val = bytes / Math.pow(1024, idx);
  return `${val >= 100 ? val.toFixed(0) : val.toFixed(1)} ${units[idx]}`;
}
