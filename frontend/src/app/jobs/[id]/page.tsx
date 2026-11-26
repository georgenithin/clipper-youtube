"use client";

import React, { use, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  AlertTriangle,
  Archive,
  ArrowLeft,
  CheckCircle2,
  CheckSquare,
  ChevronDown,
  ChevronUp,
  Loader2,
  RefreshCw,
  Square,
  Terminal,
} from "lucide-react";
import {
  downloadJobZip,
  formatDuration,
  getJob,
  jobEventsUrl,
  retryJob,
} from "@/lib/api";
import type { Clip, Job, JobLogEntry, SSEEvent } from "@/lib/types";
import { ClipCard } from "@/components/ClipCard";
import { TrimRerenderModal } from "@/components/TrimRerenderModal";
import { useToast } from "@/components/ToastProvider";

const STAGES = [
  { key: "downloading", label: "Downloading" },
  { key: "transcribing", label: "Transcribing" },
  { key: "analyzing", label: "Analyzing" },
  { key: "scoring", label: "Scoring" },
  { key: "rendering", label: "Rendering" },
  { key: "metadata", label: "Writing Metadata" },
];

function stageIndex(stage: string): number {
  if (stage === "completed") return STAGES.length;
  const idx = STAGES.findIndex((s) => s.key === stage);
  return idx === -1 ? 0 : idx;
}

export default function JobPage({ params }: { params: Promise<{ id: string }> }) {
  const { id: jobId } = use(params);
  const { toast } = useToast();

  const [job, setJob] = useState<Job | null>(null);
  const [logs, setLogs] = useState<JobLogEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showLogs, setShowLogs] = useState(false);

  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [zipBusy, setZipBusy] = useState(false);
  const [retrying, setRetrying] = useState(false);
  const [rerenderTarget, setRerenderTarget] = useState<Clip | null>(null);

  const loadJob = useCallback(async () => {
    try {
      const data = await getJob(jobId);
      setJob(data);
      setLogs(data.logs || []);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load job");
    } finally {
      setLoading(false);
    }
  }, [jobId]);

  useEffect(() => {
    loadJob();
  }, [loadJob]);

  // Live SSE progress subscription while awaiting_upload/queued/running
  useEffect(() => {
    if (!job || (job.status !== "awaiting_upload" && job.status !== "queued" && job.status !== "running")) return;

    const es = new EventSource(jobEventsUrl(jobId));
    es.onmessage = (e) => {
      try {
        const ev = JSON.parse(e.data) as SSEEvent;
        setJob((prev) =>
          prev
            ? {
                ...prev,
                status: ev.status,
                stage: ev.stage,
                progress: ev.progress,
                status_message: ev.message,
                error_message: ev.error ?? prev.error_message,
              }
            : prev
        );
        if (ev.log) {
          setLogs((prev) => [...prev.slice(-199), ev.log!]);
        }
        if (ev.status === "completed" || ev.status === "failed") {
          es.close();
          loadJob();
        }
      } catch {
        // ignore keepalive/malformed frames
      }
    };
    es.onerror = () => {
      es.close();
      // Fallback poll after 3s if SSE drops
      setTimeout(() => loadJob(), 3000);
    };

    return () => es.close();
  }, [job?.status, jobId, loadJob]);

  const handleRetry = async () => {
    setRetrying(true);
    try {
      const updated = await retryJob(jobId);
      setJob(updated);
      toast("Job re-queued (cached downloads/transcripts will be reused)", "info");
    } catch (err) {
      toast(err instanceof Error ? err.message : "Retry failed", "error");
    } finally {
      setRetrying(false);
    }
  };

  const toggleSelect = (clipId: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(clipId)) next.delete(clipId);
      else next.add(clipId);
      return next;
    });
  };

  const clips = job?.clips || [];
  const allSelected = clips.length > 0 && selectedIds.size === clips.length;

  const toggleSelectAll = () => {
    if (allSelected) setSelectedIds(new Set());
    else setSelectedIds(new Set(clips.map((c) => c.id)));
  };

  const handleDownloadZip = async (ids: string[]) => {
    setZipBusy(true);
    try {
      await downloadJobZip(jobId, ids);
      toast("ZIP archive download started!", "success");
    } catch (err) {
      toast(err instanceof Error ? err.message : "ZIP export failed", "error");
    } finally {
      setZipBusy(false);
    }
  };

  const handleClipUpdated = (updated: Clip) => {
    setJob((prev) =>
      prev
        ? {
            ...prev,
            clips: (prev.clips || []).map((c) => (c.id === updated.id ? updated : c)),
          }
        : prev
    );
  };

  if (loading) {
    return (
      <div className="space-y-6">
        <div className="h-28 animate-pulse rounded-2xl bg-[#11131b]" />
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {[1, 2, 3].map((n) => (
            <div key={n} className="h-[520px] animate-pulse rounded-2xl bg-[#11131b]" />
          ))}
        </div>
      </div>
    );
  }

  if (error || !job) {
    return (
      <div className="rounded-2xl border border-rose-500/30 bg-rose-950/30 p-8 text-center space-y-4">
        <AlertTriangle className="mx-auto h-8 w-8 text-rose-400" />
        <p className="text-sm text-rose-200">{error || "Job not found"}</p>
        <Link
          href="/"
          className="inline-flex items-center gap-1.5 rounded-lg bg-white/10 px-4 py-2 text-xs font-medium text-white hover:bg-white/15"
        >
          <ArrowLeft className="h-3.5 w-3.5" />
          Back to Studio
        </Link>
      </div>
    );
  }

  const currentIdx = stageIndex(job.stage);

  return (
    <div className="space-y-8">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div className="space-y-1 min-w-0">
          <Link
            href="/"
            className="inline-flex items-center gap-1.5 text-xs font-medium text-slate-400 hover:text-white transition-colors"
          >
            <ArrowLeft className="h-3.5 w-3.5" />
            All Jobs
          </Link>
          <h1 className="text-xl sm:text-2xl font-bold text-white truncate">
            {job.video_title || job.url}
          </h1>
          <div className="flex flex-wrap items-center gap-2 text-xs text-slate-400">
            {job.channel && <span>{job.channel}</span>}
            {job.duration > 0 && (
              <>
                <span>·</span>
                <span>{formatDuration(job.duration)}</span>
              </>
            )}
            <span>·</span>
            <a
              href={job.url}
              target="_blank"
              rel="noreferrer"
              className="text-indigo-400 hover:underline truncate max-w-xs"
            >
              YouTube Source
            </a>
          </div>
        </div>
      </div>

      {/* Live Progress Stepper */}
      {(job.status === "awaiting_upload" || job.status === "queued" || job.status === "running") && (
        <section className="rounded-2xl border border-white/[0.08] bg-[#11131b] p-6 space-y-6">
          <div className="flex items-center justify-between gap-4">
            <div className="flex items-center gap-3">
              <Loader2 className="h-5 w-5 animate-spin text-indigo-400" />
              <div>
                <p className="text-sm font-semibold text-white">{job.status_message}</p>
                <p className="text-xs text-slate-400 capitalize">Stage: {job.stage}</p>
              </div>
            </div>
            <span className="text-lg font-bold tabular-nums text-indigo-300">
              {Math.round(job.progress)}%
            </span>
          </div>

          {/* Progress Bar */}
          <div className="h-2 w-full overflow-hidden rounded-full bg-white/10">
            <div
              className="h-full rounded-full bg-gradient-to-r from-indigo-500 via-violet-500 to-amber-400 transition-all duration-300"
              style={{ width: `${Math.max(2, Math.min(100, job.progress))}%` }}
            />
          </div>

          {/* 6-Stage Stepper */}
          <ol className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-2.5">
            {STAGES.map((st, idx) => {
              const done = idx < currentIdx;
              const active = idx === currentIdx && job.status === "running";
              return (
                <li
                  key={st.key}
                  className={`flex items-center gap-2 rounded-xl border px-3 py-2.5 text-xs font-medium transition-colors ${
                    done
                      ? "border-emerald-500/25 bg-emerald-500/10 text-emerald-300"
                      : active
                      ? "border-indigo-400/50 bg-indigo-500/15 text-white"
                      : "border-white/[0.06] bg-[#0b0d14] text-slate-500"
                  }`}
                >
                  {done ? (
                    <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-400" />
                  ) : active ? (
                    <Loader2 className="h-4 w-4 shrink-0 animate-spin text-indigo-400" />
                  ) : (
                    <span className="h-2 w-2 rounded-full bg-slate-600 shrink-0" />
                  )}
                  <span className="truncate">{st.label}</span>
                </li>
              );
            })}
          </ol>

          {/* Expandable Logs */}
          <div className="border-t border-white/[0.06] pt-3">
            <button
              type="button"
              onClick={() => setShowLogs((s) => !s)}
              className="inline-flex items-center gap-2 text-xs font-medium text-slate-400 hover:text-white"
            >
              <Terminal className="h-3.5 w-3.5" />
              <span>Live Pipeline Logs ({logs.length})</span>
              {showLogs ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
            </button>
            {showLogs && (
              <div className="mt-3 max-h-52 overflow-y-auto rounded-xl border border-white/[0.06] bg-[#07080c] p-3 font-mono text-[11px] text-slate-300 space-y-1">
                {logs.map((l, i) => (
                  <div key={i} className="flex gap-2">
                    <span className="text-slate-600 shrink-0">{l.time}</span>
                    <span className="text-indigo-400 shrink-0">[{l.stage}]</span>
                    <span className="break-all">{l.message}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </section>
      )}

      {/* Failure Banner */}
      {job.status === "failed" && (
        <section className="rounded-2xl border border-rose-500/35 bg-rose-950/35 p-6 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
          <div className="flex items-start gap-3">
            <AlertTriangle className="h-5 w-5 text-rose-400 shrink-0 mt-0.5" />
            <div className="space-y-1">
              <h2 className="text-sm font-semibold text-rose-100">Processing Failed</h2>
              <p className="text-xs text-rose-200/85">
                {job.error_message || "An unexpected error occurred during processing."}
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={handleRetry}
            disabled={retrying}
            className="inline-flex items-center gap-2 rounded-xl bg-rose-500 px-4 py-2 text-xs font-semibold text-white hover:bg-rose-400 disabled:opacity-50 shrink-0"
          >
            {retrying ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
            Retry Job
          </button>
        </section>
      )}

      {/* Results Grid */}
      {clips.length > 0 && (
        <section className="space-y-4">
          <div className="flex items-center justify-between">
            <h2 className="text-lg font-semibold text-white">
              Generated Clips ({clips.length})
            </h2>
            <button
              type="button"
              onClick={toggleSelectAll}
              className="inline-flex items-center gap-1.5 text-xs font-medium text-slate-300 hover:text-white"
            >
              {allSelected ? (
                <CheckSquare className="h-4 w-4 text-indigo-400" />
              ) : (
                <Square className="h-4 w-4 text-slate-500" />
              )}
              {allSelected ? "Deselect All" : "Select All"}
            </button>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-6">
            {clips.map((clip) => (
              <ClipCard
                key={clip.id}
                clip={clip}
                selected={selectedIds.has(clip.id)}
                onToggleSelect={toggleSelect}
                onOpenRerender={setRerenderTarget}
                onMetadataSaved={handleClipUpdated}
              />
            ))}
          </div>
        </section>
      )}

      {/* Sticky Bulk Action Bar */}
      {clips.length > 0 && (
        <div className="fixed bottom-4 left-1/2 -translate-x-1/2 z-30 w-[min(94vw,44rem)] rounded-2xl border border-white/12 bg-[#141722]/95 px-4 py-3 shadow-2xl backdrop-blur-xl flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2.5 text-xs text-slate-300">
            <span className="inline-flex h-6 min-w-6 items-center justify-center rounded-full bg-indigo-500/20 px-2 font-bold text-indigo-300">
              {selectedIds.size}
            </span>
            <span>selected of {clips.length} clips</span>
          </div>

          <div className="flex items-center gap-2">
            <button
              type="button"
              disabled={selectedIds.size === 0 || zipBusy}
              onClick={() => handleDownloadZip(Array.from(selectedIds))}
              className="inline-flex items-center gap-1.5 rounded-xl border border-white/12 bg-white/[0.06] px-3.5 py-2 text-xs font-semibold text-white hover:bg-white/12 disabled:opacity-40 transition-colors"
            >
              {zipBusy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Archive className="h-3.5 w-3.5" />}
              Download Selected (ZIP)
            </button>
            <button
              type="button"
              disabled={zipBusy}
              onClick={() => handleDownloadZip([])}
              className="inline-flex items-center gap-1.5 rounded-xl bg-indigo-500 px-3.5 py-2 text-xs font-semibold text-white hover:bg-indigo-400 disabled:opacity-50 transition-colors shadow-lg shadow-indigo-500/25"
            >
              {zipBusy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Archive className="h-3.5 w-3.5" />}
              Download All (ZIP)
            </button>
          </div>
        </div>
      )}

      {/* Trim & Re-render Native <dialog> Modal */}
      <TrimRerenderModal
        clip={rerenderTarget}
        videoDuration={job.duration}
        onClose={() => setRerenderTarget(null)}
        onUpdated={handleClipUpdated}
      />
    </div>
  );
}
