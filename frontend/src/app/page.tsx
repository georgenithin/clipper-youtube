"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  ChevronDown,
  ChevronUp,
  ClipboardPaste,
  Clock,
  Film,
  Loader2,
  Play,
  SlidersHorizontal,
  Sparkles,
  Trash2,
  Youtube,
} from "lucide-react";
import { createJob, deleteJob, formatDuration, getSettings, listJobs } from "@/lib/api";
import type { Job, JobOptions } from "@/lib/types";
import { useToast } from "@/components/ToastProvider";

const DEFAULT_OPTIONS: JobOptions = {
  clip_count: 5,
  min_clip_sec: 20,
  max_clip_sec: 60,
  output_format: "9:16",
  crop_mode: "center",
  caption_style: "bold_yellow",
  captions_enabled: true,
  quality: "1080p",
  language: "",
  denoise_audio: false,
};

export default function HomePage() {
  const router = useRouter();
  const { toast } = useToast();

  const [url, setUrl] = useState("");
  const [options, setOptions] = useState<JobOptions>(DEFAULT_OPTIONS);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [loadingJobs, setLoadingJobs] = useState(true);

  useEffect(() => {
    let active = true;
    Promise.all([listJobs().catch(() => []), getSettings().catch(() => null)]).then(
      ([jobList, settings]) => {
        if (!active) return;
        setJobs(jobList);
        if (settings) {
          setOptions((prev) => ({
            ...prev,
            output_format: settings.default_format || prev.output_format,
            caption_style: settings.default_caption_style || prev.caption_style,
            whisper_model: settings.whisper_model,
          }));
        }
        setLoadingJobs(false);
      }
    );
    return () => {
      active = false;
    };
  }, []);

  const handlePaste = async () => {
    try {
      const text = await navigator.clipboard.readText();
      if (text) {
        setUrl(text.trim());
        toast("Pasted link from clipboard", "info");
      }
    } catch {
      toast("Clipboard read blocked — paste manually with Cmd/Ctrl+V", "error");
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!url.trim()) {
      toast("Paste a YouTube link first", "error");
      return;
    }
    setSubmitting(true);
    try {
      const payload: JobOptions = {
        ...options,
        language: options.language?.trim() || null,
      };
      const job = await createJob(url.trim(), payload);
      toast("Job queued! Opening live studio...", "success");
      router.push(`/jobs/${job.id}`);
    } catch (err) {
      toast(err instanceof Error ? err.message : "Could not start job", "error");
      setSubmitting(false);
    }
  };

  const handleDelete = async (jobId: string, e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    try {
      await deleteJob(jobId);
      setJobs((prev) => prev.filter((j) => j.id !== jobId));
      toast("Job deleted", "info");
    } catch (err) {
      toast(err instanceof Error ? err.message : "Delete failed", "error");
    }
  };

  return (
    <div className="space-y-12">
      {/* Hero Input */}
      <section className="cf-hero-glow relative overflow-hidden rounded-3xl border border-white/[0.08] bg-[#10121a] p-6 sm:p-10 shadow-2xl">
        <div className="mx-auto max-w-3xl text-center space-y-4">
          <div className="inline-flex items-center gap-2 rounded-full border border-indigo-400/25 bg-indigo-500/10 px-3 py-1 text-xs font-medium text-indigo-300">
            <Sparkles className="h-3.5 w-3.5" />
            <span>100% Local · Whisper + FFmpeg + Gemini Free Tier</span>
          </div>
          <h1 className="text-3xl sm:text-5xl font-bold tracking-tight text-white">
            Turn long YouTube videos into{" "}
            <span className="bg-gradient-to-r from-indigo-400 via-violet-300 to-amber-300 bg-clip-text text-transparent">
              viral vertical clips
            </span>
          </h1>
          <p className="text-sm sm:text-base text-slate-400 max-w-2xl mx-auto">
            Paste a YouTube link. ClipForge finds the highest-retention moments, burns in
            word-highlighted captions, normalizes audio to -14 LUFS, and drafts Shorts &amp; Reels copy.
          </p>
        </div>

        <form onSubmit={handleSubmit} className="mx-auto mt-8 max-w-3xl space-y-4">
          <div className="flex flex-col sm:flex-row gap-2.5 rounded-2xl border border-white/12 bg-[#090a0f] p-2 shadow-inner focus-within:border-indigo-400/70 transition-colors">
            <div className="flex flex-1 items-center gap-2.5 px-2.5">
              <Youtube className="h-5 w-5 text-rose-500 shrink-0" />
              <input
                type="url"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                placeholder="https://www.youtube.com/watch?v=..."
                aria-label="YouTube video URL"
                required
                className="w-full bg-transparent py-2.5 text-sm sm:text-base text-white placeholder:text-slate-500 focus:outline-none"
              />
              <button
                type="button"
                onClick={handlePaste}
                className="inline-flex items-center gap-1.5 rounded-lg border border-white/10 bg-white/[0.04] px-2.5 py-1.5 text-xs font-medium text-slate-300 hover:bg-white/10 hover:text-white transition-colors shrink-0"
              >
                <ClipboardPaste className="h-3.5 w-3.5" />
                Paste
              </button>
            </div>
            <button
              type="submit"
              disabled={submitting}
              className="inline-flex items-center justify-center gap-2 rounded-xl bg-gradient-to-r from-indigo-500 to-violet-500 px-6 py-3 text-sm font-semibold text-white hover:from-indigo-400 hover:to-violet-400 disabled:opacity-50 transition-all shadow-lg shadow-indigo-500/25 shrink-0 cursor-pointer"
            >
              {submitting ? (
                <>
                  <Loader2 className="h-4 w-4 animate-spin" />
                  Starting...
                </>
              ) : (
                <>
                  <Sparkles className="h-4 w-4" />
                  Forge Clips
                </>
              )}
            </button>
          </div>

          {/* Options Drawer Toggle */}
          <div className="flex items-center justify-between px-1">
            <button
              type="button"
              onClick={() => setDrawerOpen((o) => !o)}
              aria-expanded={drawerOpen}
              className="inline-flex items-center gap-2 text-xs font-medium text-slate-300 hover:text-white transition-colors"
            >
              <SlidersHorizontal className="h-3.5 w-3.5 text-indigo-400" />
              <span>Clip &amp; Render Options</span>
              {drawerOpen ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
            </button>

            <div className="hidden sm:flex items-center gap-2 text-xs text-slate-500">
              <span>{options.clip_count} clips</span>
              <span>·</span>
              <span>
                {options.min_clip_sec}–{options.max_clip_sec}s
              </span>
              <span>·</span>
              <span>{options.output_format}</span>
              <span>·</span>
              <span>{options.quality}</span>
            </div>
          </div>

          {/* Collapsible Options Drawer */}
          {drawerOpen && (
            <div className="rounded-2xl border border-white/10 bg-[#0b0d14] p-5 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4 text-sm">
              <div className="space-y-1.5">
                <label htmlFor="opt-count" className="block text-xs font-medium text-slate-300">
                  Clip Count ({options.clip_count})
                </label>
                <input
                  id="opt-count"
                  type="range"
                  min={1}
                  max={10}
                  value={options.clip_count}
                  onChange={(e) => setOptions({ ...options, clip_count: Number(e.target.value) })}
                  className="w-full"
                />
              </div>

              <div className="space-y-1.5">
                <label className="block text-xs font-medium text-slate-300">
                  Clip Length (seconds)
                </label>
                <div className="flex items-center gap-2">
                  <input
                    type="number"
                    min={5}
                    max={110}
                    value={options.min_clip_sec}
                    onChange={(e) => setOptions({ ...options, min_clip_sec: Number(e.target.value) })}
                    aria-label="Minimum clip seconds"
                    className="w-full rounded-lg border border-white/10 bg-[#121520] px-2.5 py-1.5 text-xs text-white"
                  />
                  <span className="text-slate-500">to</span>
                  <input
                    type="number"
                    min={15}
                    max={180}
                    value={options.max_clip_sec}
                    onChange={(e) => setOptions({ ...options, max_clip_sec: Number(e.target.value) })}
                    aria-label="Maximum clip seconds"
                    className="w-full rounded-lg border border-white/10 bg-[#121520] px-2.5 py-1.5 text-xs text-white"
                  />
                </div>
              </div>

              <div className="space-y-1.5">
                <label htmlFor="opt-format" className="block text-xs font-medium text-slate-300">
                  Aspect Ratio
                </label>
                <select
                  id="opt-format"
                  value={options.output_format}
                  onChange={(e) =>
                    setOptions({ ...options, output_format: e.target.value as JobOptions["output_format"] })
                  }
                  className="w-full rounded-lg border border-white/10 bg-[#121520] px-2.5 py-1.5 text-xs text-white"
                >
                  <option value="9:16">9:16 Vertical (1080×1920)</option>
                  <option value="1:1">1:1 Square (1080×1080)</option>
                  <option value="16:9">16:9 Original</option>
                </select>
              </div>

              <div className="space-y-1.5">
                <label htmlFor="opt-crop" className="block text-xs font-medium text-slate-300">
                  Framing / Smart Crop
                </label>
                <select
                  id="opt-crop"
                  value={options.crop_mode}
                  onChange={(e) =>
                    setOptions({ ...options, crop_mode: e.target.value as JobOptions["crop_mode"] })
                  }
                  className="w-full rounded-lg border border-white/10 bg-[#121520] px-2.5 py-1.5 text-xs text-white"
                >
                  <option value="center">Center Crop-to-Fill</option>
                  <option value="smart">Smart Crop (MediaPipe Face)</option>
                  <option value="blur_fit">Blurred Background Fit</option>
                </select>
              </div>

              <div className="space-y-1.5">
                <label htmlFor="opt-style" className="block text-xs font-medium text-slate-300">
                  Caption Style
                </label>
                <select
                  id="opt-style"
                  value={options.caption_style}
                  onChange={(e) =>
                    setOptions({ ...options, caption_style: e.target.value as JobOptions["caption_style"] })
                  }
                  className="w-full rounded-lg border border-white/10 bg-[#121520] px-2.5 py-1.5 text-xs text-white"
                >
                  <option value="bold_yellow">Bold Yellow Highlight</option>
                  <option value="clean">Clean</option>
                  <option value="minimal">Minimal</option>
                </select>
              </div>

              <div className="space-y-1.5">
                <label htmlFor="opt-quality" className="block text-xs font-medium text-slate-300">
                  Download Quality
                </label>
                <select
                  id="opt-quality"
                  value={options.quality}
                  onChange={(e) =>
                    setOptions({ ...options, quality: e.target.value as JobOptions["quality"] })
                  }
                  className="w-full rounded-lg border border-white/10 bg-[#121520] px-2.5 py-1.5 text-xs text-white"
                >
                  <option value="1080p">1080p (Full HD)</option>
                  <option value="720p">720p (Faster)</option>
                </select>
              </div>

              <div className="space-y-1.5">
                <label htmlFor="opt-lang" className="block text-xs font-medium text-slate-300">
                  Language (blank = auto)
                </label>
                <input
                  id="opt-lang"
                  type="text"
                  placeholder="e.g. en, es, fr"
                  value={options.language || ""}
                  onChange={(e) => setOptions({ ...options, language: e.target.value })}
                  className="w-full rounded-lg border border-white/10 bg-[#121520] px-2.5 py-1.5 text-xs text-white placeholder:text-slate-500"
                />
              </div>

              <div className="flex flex-col justify-end gap-2 sm:col-span-2">
                <div className="flex flex-wrap gap-5">
                  <label className="inline-flex items-center gap-2 text-xs text-slate-200 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={options.captions_enabled}
                      onChange={(e) => setOptions({ ...options, captions_enabled: e.target.checked })}
                      className="h-4 w-4 rounded"
                    />
                    <span>Burn-in captions</span>
                  </label>
                  <label className="inline-flex items-center gap-2 text-xs text-slate-200 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={options.denoise_audio}
                      onChange={(e) => setOptions({ ...options, denoise_audio: e.target.checked })}
                      className="h-4 w-4 rounded"
                    />
                    <span>Light audio denoise</span>
                  </label>
                </div>
              </div>
            </div>
          )}
        </form>
      </section>

      {/* Recent Jobs */}
      <section className="space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold text-white">Recent Jobs</h2>
          <span className="text-xs text-slate-500">{jobs.length} total</span>
        </div>

        {loadingJobs ? (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {[1, 2, 3].map((n) => (
              <div
                key={n}
                className="h-36 animate-pulse rounded-2xl border border-white/[0.06] bg-[#11131b]"
              />
            ))}
          </div>
        ) : jobs.length === 0 ? (
          <div className="rounded-2xl border border-dashed border-white/10 bg-[#0d0f16] p-12 text-center space-y-2">
            <Film className="mx-auto h-8 w-8 text-slate-600" />
            <p className="text-sm font-medium text-slate-300">No jobs yet</p>
            <p className="text-xs text-slate-500">
              Paste any YouTube URL above to generate your first batch of short-form clips.
            </p>
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {jobs.map((job) => (
              <Link
                key={job.id}
                href={`/jobs/${job.id}`}
                className="group flex flex-col justify-between rounded-2xl border border-white/[0.08] bg-[#11131b] p-4 hover:border-indigo-400/40 transition-all"
              >
                <div className="flex gap-3.5">
                  <div className="relative h-16 w-28 shrink-0 overflow-hidden rounded-lg bg-[#090a0f] border border-white/[0.06]">
                    {job.thumbnail_url ? (
                      <img
                        src={job.thumbnail_url}
                        alt=""
                        className="h-full w-full object-cover group-hover:scale-105 transition-transform"
                      />
                    ) : (
                      <div className="flex h-full w-full items-center justify-center text-slate-600">
                        <Play className="h-5 w-5" />
                      </div>
                    )}
                  </div>
                  <div className="min-w-0 flex-1 space-y-1">
                    <h3 className="truncate text-sm font-semibold text-white group-hover:text-indigo-300 transition-colors">
                      {job.video_title || job.url}
                    </h3>
                    <p className="truncate text-xs text-slate-400">
                      {job.channel || job.video_id || "YouTube"}
                    </p>
                    <div className="flex items-center gap-2 pt-0.5">
                      <StatusBadge status={job.status} />
                      {job.duration > 0 && (
                        <span className="inline-flex items-center gap-1 text-[11px] text-slate-500">
                          <Clock className="h-3 w-3" />
                          {formatDuration(job.duration)}
                        </span>
                      )}
                    </div>
                  </div>
                </div>

                <div className="mt-4 flex items-center justify-between border-t border-white/[0.06] pt-3 text-xs text-slate-400">
                  <span>
                    {job.status === "completed"
                      ? `${job.clip_count} clips ready`
                      : job.status === "running"
                      ? `${Math.round(job.progress)}% · ${job.stage}`
                      : job.status === "failed"
                      ? "Failed — click to retry"
                      : "Queued"}
                  </span>
                  <button
                    type="button"
                    onClick={(e) => handleDelete(job.id, e)}
                    aria-label={`Delete job ${job.video_title || job.id}`}
                    className="rounded-md p-1 text-slate-500 hover:bg-rose-500/15 hover:text-rose-400 transition-colors"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </div>
              </Link>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}

function StatusBadge({ status }: { status: Job["status"] }) {
  const map: Record<Job["status"], string> = {
    completed: "bg-emerald-500/15 text-emerald-300 border-emerald-500/25",
    running: "bg-indigo-500/15 text-indigo-300 border-indigo-500/25",
    queued: "bg-amber-500/15 text-amber-300 border-amber-500/25",
    failed: "bg-rose-500/15 text-rose-300 border-rose-500/25",
  };
  return (
    <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${map[status]}`}>
      {status}
    </span>
  );
}
