"use client";

import React, { useEffect, useId, useRef, useState } from "react";
import { Sliders, X, Loader2, Sparkles } from "lucide-react";
import type { Clip } from "@/lib/types";
import { formatDuration, rerenderClip } from "@/lib/api";
import { useToast } from "./ToastProvider";

interface TrimRerenderModalProps {
  clip: Clip | null;
  videoDuration: number;
  onClose: () => void;
  onUpdated: (clip: Clip) => void;
}

export function TrimRerenderModal({
  clip,
  videoDuration,
  onClose,
  onUpdated,
}: TrimRerenderModalProps) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const { toast } = useToast();

  const [startSec, setStartSec] = useState(0);
  const [endSec, setEndSec] = useState(30);
  const [outputFormat, setOutputFormat] = useState<"9:16" | "1:1" | "16:9">("9:16");
  const [cropMode, setCropMode] = useState<"center" | "smart" | "blur_fit">("center");
  const [captionStyle, setCaptionStyle] = useState<"bold_yellow" | "clean" | "minimal">("bold_yellow");
  const [captionsEnabled, setCaptionsEnabled] = useState(true);
  const [denoiseAudio, setDenoiseAudio] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  const maxDur = Math.max(videoDuration || 0, clip?.end_sec || 60, 10);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;

    if (clip) {
      setStartSec(Number(clip.start_sec.toFixed(1)));
      setEndSec(Number(clip.end_sec.toFixed(1)));
      setOutputFormat(clip.output_format);
      setCropMode(clip.crop_mode);
      setCaptionStyle(clip.caption_style);
      setCaptionsEnabled(clip.captions_enabled);
      setDenoiseAudio(clip.denoise_audio);
      if (!dialog.open) {
        dialog.showModal();
      }
    } else if (dialog.open) {
      dialog.close();
    }
  }, [clip]);

  // Fallback light-dismiss when closedby="any" is unsupported (per modern-web-guidance)
  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if ("closedBy" in HTMLDialogElement.prototype) return;

    let pressedOutside = false;
    const isOutside = (e: PointerEvent) => {
      const r = dialog.getBoundingClientRect();
      return e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom;
    };
    const onDown = (e: PointerEvent) => {
      pressedOutside = isOutside(e);
    };
    const onUp = (e: PointerEvent) => {
      if (pressedOutside && isOutside(e)) dialog.close();
    };
    dialog.addEventListener("pointerdown", onDown);
    dialog.addEventListener("pointerup", onUp);
    return () => {
      dialog.removeEventListener("pointerdown", onDown);
      dialog.removeEventListener("pointerup", onUp);
    };
  }, []);

  const handleClose = () => {
    const dialog = dialogRef.current;
    if (dialog) {
      dialog.returnValue = "";
    }
    onClose();
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!clip) return;
    if (endSec <= startSec + 1) {
      toast("End time must be at least 1 second after start time.", "error");
      return;
    }
    setSubmitting(true);
    try {
      const updated = await rerenderClip(clip.id, {
        start_sec: startSec,
        end_sec: endSec,
        output_format: outputFormat,
        crop_mode: cropMode,
        caption_style: captionStyle,
        captions_enabled: captionsEnabled,
        denoise_audio: denoiseAudio,
      });
      onUpdated(updated);
      toast(`Clip #${clip.rank} re-rendered successfully!`, "success");
      dialogRef.current?.close();
    } catch (err) {
      toast(err instanceof Error ? err.message : "Failed to re-render clip", "error");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <dialog
      ref={dialogRef}
      aria-labelledby={titleId}
      onClose={handleClose}
      className="cf-modal"
      {...({ closedby: "any" } as Record<string, string>)}
    >
      <div className="flex items-center justify-between border-b border-white/10 px-5 py-4">
        <div className="flex items-center gap-2.5">
          <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-indigo-500/15 text-indigo-400">
            <Sliders className="h-4 w-4" />
          </span>
          <h1 id={titleId} className="text-base font-semibold text-white">
            Trim &amp; Re-render {clip ? `Clip #${clip.rank}` : ""}
          </h1>
        </div>
        <button
          type="button"
          onClick={() => dialogRef.current?.close()}
          aria-label="Close modal"
          className="rounded-lg p-1.5 text-slate-400 hover:bg-white/10 hover:text-white transition-colors"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      <form onSubmit={handleSubmit} className="overflow-y-auto p-5 space-y-5">
        {/* Trim Sliders */}
        <div className="rounded-xl border border-white/10 bg-[#0d0f16] p-4 space-y-4">
          <div className="flex items-center justify-between">
            <span className="text-xs font-semibold uppercase tracking-wider text-slate-400">
              Clip Boundaries
            </span>
            <span className="rounded-md bg-indigo-500/15 px-2 py-0.5 text-xs font-semibold tabular-nums text-indigo-300">
              Duration: {formatDuration(Math.max(0, endSec - startSec))}
            </span>
          </div>

          <div className="space-y-1.5">
            <div className="flex justify-between text-xs">
              <label htmlFor="trim-start" className="text-slate-300">
                Start Time
              </label>
              <span className="font-mono text-slate-200">{startSec.toFixed(1)}s ({formatDuration(startSec)})</span>
            </div>
            <input
              id="trim-start"
              type="range"
              min={0}
              max={Math.max(1, maxDur - 2)}
              step={0.1}
              value={startSec}
              autoFocus
              onChange={(e) => {
                const v = parseFloat(e.target.value);
                setStartSec(v);
                if (v >= endSec - 1.5) setEndSec(Math.min(maxDur, Number((v + 2).toFixed(1))));
              }}
              className="w-full cursor-pointer"
            />
          </div>

          <div className="space-y-1.5">
            <div className="flex justify-between text-xs">
              <label htmlFor="trim-end" className="text-slate-300">
                End Time
              </label>
              <span className="font-mono text-slate-200">{endSec.toFixed(1)}s ({formatDuration(endSec)})</span>
            </div>
            <input
              id="trim-end"
              type="range"
              min={Math.min(maxDur, startSec + 1.5)}
              max={maxDur}
              step={0.1}
              value={endSec}
              onChange={(e) => setEndSec(parseFloat(e.target.value))}
              className="w-full cursor-pointer"
            />
          </div>
        </div>

        {/* Format & Crop */}
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div className="space-y-1.5">
            <label htmlFor="rr-format" className="block text-xs font-medium text-slate-300">
              Aspect Ratio
            </label>
            <select
              id="rr-format"
              value={outputFormat}
              onChange={(e) => setOutputFormat(e.target.value as "9:16" | "1:1" | "16:9")}
              className="w-full rounded-lg border border-white/10 bg-[#0d0f16] px-3 py-2 text-sm text-white focus:border-indigo-400 focus:outline-none"
            >
              <option value="9:16">9:16 Vertical (Shorts / Reels)</option>
              <option value="1:1">1:1 Square</option>
              <option value="16:9">16:9 Original</option>
            </select>
          </div>

          <div className="space-y-1.5">
            <label htmlFor="rr-crop" className="block text-xs font-medium text-slate-300">
              Framing Mode
            </label>
            <select
              id="rr-crop"
              value={cropMode}
              onChange={(e) => setCropMode(e.target.value as "center" | "smart" | "blur_fit")}
              className="w-full rounded-lg border border-white/10 bg-[#0d0f16] px-3 py-2 text-sm text-white focus:border-indigo-400 focus:outline-none"
            >
              <option value="center">Center Crop-to-Fill</option>
              <option value="smart">Smart Face-Aware Crop</option>
              <option value="blur_fit">Blurred Background Fit</option>
            </select>
          </div>
        </div>

        {/* Captions & Audio */}
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-end">
          <div className="space-y-1.5">
            <label htmlFor="rr-style" className="block text-xs font-medium text-slate-300">
              Caption Style
            </label>
            <select
              id="rr-style"
              value={captionStyle}
              disabled={!captionsEnabled}
              onChange={(e) => setCaptionStyle(e.target.value as "bold_yellow" | "clean" | "minimal")}
              className="w-full rounded-lg border border-white/10 bg-[#0d0f16] px-3 py-2 text-sm text-white disabled:opacity-45 focus:border-indigo-400 focus:outline-none"
            >
              <option value="bold_yellow">Bold Yellow Highlight</option>
              <option value="clean">Clean</option>
              <option value="minimal">Minimal</option>
            </select>
          </div>

          <div className="flex flex-col gap-2.5 pb-1">
            <label className="inline-flex items-center gap-2.5 text-sm text-slate-200 cursor-pointer">
              <input
                type="checkbox"
                checked={captionsEnabled}
                onChange={(e) => setCaptionsEnabled(e.target.checked)}
                className="h-4 w-4 rounded"
              />
              <span>Burn in word-highlighted captions</span>
            </label>
            <label className="inline-flex items-center gap-2.5 text-sm text-slate-200 cursor-pointer">
              <input
                type="checkbox"
                checked={denoiseAudio}
                onChange={(e) => setDenoiseAudio(e.target.checked)}
                className="h-4 w-4 rounded"
              />
              <span>Light audio denoise</span>
            </label>
          </div>
        </div>

        <div className="flex items-center justify-end gap-2.5 border-t border-white/10 pt-4">
          <button
            type="button"
            onClick={() => dialogRef.current?.close()}
            className="rounded-lg border border-white/10 px-4 py-2 text-sm font-medium text-slate-300 hover:bg-white/5 transition-colors"
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={submitting}
            className="inline-flex items-center gap-2 rounded-lg bg-indigo-500 px-4 py-2 text-sm font-semibold text-white hover:bg-indigo-400 disabled:opacity-50 transition-colors shadow-lg shadow-indigo-500/20"
          >
            {submitting ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" />
                Re-rendering...
              </>
            ) : (
              <>
                <Sparkles className="h-4 w-4" />
                Re-render Clip
              </>
            )}
          </button>
        </div>
      </form>
    </dialog>
  );
}
