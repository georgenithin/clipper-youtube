"use client";

import React, { useEffect, useId, useRef, useState } from "react";
import Link from "next/link";
import {
  AlertCircle,
  CheckCircle2,
  ExternalLink,
  Instagram,
  Loader2,
  Send,
  X,
  Youtube,
} from "lucide-react";
import type { Clip, PublishClipResult, PublishStatus } from "@/lib/types";
import { getPublishStatus, publishClips } from "@/lib/api";
import { useToast } from "./ToastProvider";

interface PublishModalProps {
  clips: Clip[];
  open: boolean;
  onClose: () => void;
  onPublished: () => void;
}

export function PublishModal({ clips, open, onClose, onPublished }: PublishModalProps) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const { toast } = useToast();

  const [status, setStatus] = useState<PublishStatus | null>(null);
  const [postYoutube, setPostYoutube] = useState(true);
  const [postInstagram, setPostInstagram] = useState(false);
  const [privacy, setPrivacy] = useState<"private" | "unlisted" | "public">("private");
  const [useNoCaptions, setUseNoCaptions] = useState(false);
  const [busy, setBusy] = useState(false);
  const [results, setResults] = useState<PublishClipResult[] | null>(null);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (open && clips.length > 0) {
      setResults(null);
      getPublishStatus()
        .then((st) => {
          setStatus(st);
          setPostYoutube(st.youtube.connected);
          setPostInstagram(st.instagram.connected);
        })
        .catch(() => setStatus(null));
      if (!dialog.open) dialog.showModal();
    } else if (dialog.open) {
      dialog.close();
    }
  }, [open, clips.length]);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog || "closedBy" in HTMLDialogElement.prototype) return;
    let pressedOutside = false;
    const outside = (e: PointerEvent) => {
      const r = dialog.getBoundingClientRect();
      return e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom;
    };
    const onDown = (e: PointerEvent) => {
      pressedOutside = outside(e);
    };
    const onUp = (e: PointerEvent) => {
      if (pressedOutside && outside(e)) dialog.close();
    };
    dialog.addEventListener("pointerdown", onDown);
    dialog.addEventListener("pointerup", onUp);
    return () => {
      dialog.removeEventListener("pointerdown", onDown);
      dialog.removeEventListener("pointerup", onUp);
    };
  }, []);

  const handleClose = () => {
    if (dialogRef.current) dialogRef.current.returnValue = "";
    onClose();
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const platforms: ("youtube" | "instagram")[] = [];
    if (postYoutube) platforms.push("youtube");
    if (postInstagram) platforms.push("instagram");
    if (platforms.length === 0) {
      toast("Select at least one platform to publish to.", "error");
      return;
    }

    setBusy(true);
    setResults(null);
    try {
      const res = await publishClips({
        clip_ids: clips.map((c) => c.id),
        platforms,
        privacy_status: privacy,
        use_nocaptions: useNoCaptions,
      });
      setResults(res.results);
      const okCount = res.results.filter((r) => r.ok).length;
      if (okCount === res.results.length) {
        toast(`Published ${okCount} clip${okCount > 1 ? "s" : ""}!`, "success");
      } else {
        toast(`Published ${okCount}/${res.results.length} clips — check details below.`, "info");
      }
      onPublished();
    } catch (err) {
      toast(err instanceof Error ? err.message : "Publishing failed", "error");
    } finally {
      setBusy(false);
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
          <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-gradient-to-br from-rose-500/20 to-amber-500/20 text-rose-400">
            <Send className="h-4 w-4" />
          </span>
          <h1 id={titleId} className="text-base font-semibold text-white">
            Post {clips.length} {clips.length === 1 ? "Clip" : "Clips"} to Social
          </h1>
        </div>
        <button
          type="button"
          onClick={() => dialogRef.current?.close()}
          aria-label="Close publish dialog"
          className="rounded-lg p-1.5 text-slate-400 hover:bg-white/10 hover:text-white"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      <form onSubmit={handleSubmit} className="overflow-y-auto p-5 space-y-5">
        {/* Selected Clips Summary */}
        <div className="space-y-2">
          <p className="text-xs font-semibold uppercase tracking-wider text-slate-400">
            Selected Clips (uses your edited title, caption &amp; hashtags)
          </p>
          <div className="max-h-36 overflow-y-auto space-y-1.5 rounded-xl border border-white/10 bg-[#0b0d14] p-2.5">
            {clips.map((c) => (
              <div key={c.id} className="flex items-center gap-2 text-xs">
                <span className="rounded bg-indigo-500/20 px-1.5 py-0.5 font-bold text-indigo-300">
                  #{c.rank}
                </span>
                <span className="truncate text-slate-200">
                  {c.metadata.youtube_shorts?.title || `Clip #${c.rank}`}
                </span>
              </div>
            ))}
          </div>
        </div>

        {/* Platform Selection */}
        <div className="space-y-3">
          {/* YouTube Shorts */}
          <div className="rounded-xl border border-white/10 bg-[#0d0f16] p-4 space-y-3">
            <div className="flex items-center justify-between gap-2">
              <label className="inline-flex items-center gap-2.5 text-sm font-semibold text-white cursor-pointer">
                <input
                  type="checkbox"
                  checked={postYoutube}
                  disabled={!status?.youtube.connected}
                  onChange={(e) => setPostYoutube(e.target.checked)}
                  className="h-4 w-4 rounded"
                />
                <Youtube className="h-4 w-4 text-rose-500" />
                <span>YouTube Shorts</span>
              </label>
              {status?.youtube.connected ? (
                <span className="rounded-full bg-emerald-500/15 px-2.5 py-0.5 text-[11px] font-medium text-emerald-300">
                  {status.youtube.channel_title || "Connected"}
                </span>
              ) : (
                <Link
                  href="/settings"
                  onClick={() => dialogRef.current?.close()}
                  className="text-xs font-medium text-indigo-400 hover:underline"
                >
                  Connect in Settings →
                </Link>
              )}
            </div>

            {postYoutube && status?.youtube.connected && (
              <div className="flex items-center justify-between gap-3 pt-1 border-t border-white/[0.06]">
                <label htmlFor="pub-privacy" className="text-xs text-slate-400">
                  Visibility
                </label>
                <select
                  id="pub-privacy"
                  value={privacy}
                  onChange={(e) => setPrivacy(e.target.value as "private" | "unlisted" | "public")}
                  className="rounded-lg border border-white/10 bg-[#131620] px-2.5 py-1 text-xs text-white"
                >
                  <option value="private">Private (review in Studio first)</option>
                  <option value="unlisted">Unlisted</option>
                  <option value="public">Public (live immediately)</option>
                </select>
              </div>
            )}
          </div>

          {/* Instagram Reels */}
          <div className="rounded-xl border border-white/10 bg-[#0d0f16] p-4">
            <div className="flex items-center justify-between gap-2">
              <label className="inline-flex items-center gap-2.5 text-sm font-semibold text-white cursor-pointer">
                <input
                  type="checkbox"
                  checked={postInstagram}
                  disabled={!status?.instagram.connected}
                  onChange={(e) => setPostInstagram(e.target.checked)}
                  className="h-4 w-4 rounded"
                />
                <Instagram className="h-4 w-4 text-pink-400" />
                <span>Instagram Reels</span>
              </label>
              {status?.instagram.connected ? (
                <span className="rounded-full bg-emerald-500/15 px-2.5 py-0.5 text-[11px] font-medium text-emerald-300">
                  Connected ({status.instagram.ig_user_id})
                </span>
              ) : (
                <Link
                  href="/settings"
                  onClick={() => dialogRef.current?.close()}
                  className="text-xs font-medium text-indigo-400 hover:underline"
                >
                  Connect in Settings →
                </Link>
              )}
            </div>
          </div>
        </div>

        <label className="inline-flex items-center gap-2 text-xs text-slate-300 cursor-pointer">
          <input
            type="checkbox"
            checked={useNoCaptions}
            onChange={(e) => setUseNoCaptions(e.target.checked)}
            className="h-4 w-4 rounded"
          />
          <span>Upload clean video (without burned-in captions)</span>
        </label>

        {/* Results */}
        {results && (
          <div className="space-y-2 rounded-xl border border-white/10 bg-[#090a0f] p-3.5">
            <p className="text-xs font-semibold text-white">Publish Results</p>
            {results.map((r) => (
              <div key={r.clip_id} className="space-y-1 text-xs border-t border-white/[0.06] pt-2 first:border-0 first:pt-0">
                <div className="font-semibold text-slate-200">Clip #{r.rank ?? "?"}</div>
                {r.error && <p className="text-rose-300">{r.error}</p>}
                {r.platforms &&
                  Object.entries(r.platforms).map(([plat, out]) => (
                    <div key={plat} className="flex items-center justify-between gap-2">
                      <span className="capitalize text-slate-400">{plat}:</span>
                      {out.ok ? (
                        <a
                          href={out.url}
                          target="_blank"
                          rel="noreferrer"
                          className="inline-flex items-center gap-1 text-emerald-300 hover:underline"
                        >
                          <CheckCircle2 className="h-3.5 w-3.5" />
                          View Post
                          <ExternalLink className="h-3 w-3" />
                        </a>
                      ) : (
                        <span className="inline-flex items-center gap-1 text-rose-300">
                          <AlertCircle className="h-3.5 w-3.5 shrink-0" />
                          {out.error}
                        </span>
                      )}
                    </div>
                  ))}
              </div>
            ))}
          </div>
        )}

        <div className="flex items-center justify-end gap-2.5 border-t border-white/10 pt-4">
          <button
            type="button"
            onClick={() => dialogRef.current?.close()}
            className="rounded-lg border border-white/10 px-4 py-2 text-sm font-medium text-slate-300 hover:bg-white/5"
          >
            Close
          </button>
          <button
            type="submit"
            disabled={busy || (!postYoutube && !postInstagram)}
            className="inline-flex items-center gap-2 rounded-lg bg-gradient-to-r from-rose-500 to-indigo-500 px-4 py-2 text-sm font-semibold text-white hover:from-rose-400 hover:to-indigo-400 disabled:opacity-45 shadow-lg"
          >
            {busy ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" />
                Publishing...
              </>
            ) : (
              <>
                <Send className="h-4 w-4" />
                Post Now
              </>
            )}
          </button>
        </div>
      </form>
    </dialog>
  );
}
