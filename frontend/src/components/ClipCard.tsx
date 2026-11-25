"use client";

import React, { useState } from "react";
import {
  Check,
  ChevronDown,
  Copy,
  Download,
  Film,
  FileText,
  Image as ImageIcon,
  Music,
  Save,
  Sliders,
  Sparkles,
  Subtitles,
} from "lucide-react";
import type { Clip, ClipMetadata } from "@/lib/types";
import { clipDownloadUrl, formatDuration, updateClipMetadata } from "@/lib/api";
import { ScoreBreakdown, ScoreRing } from "./ScoreRing";
import { useToast } from "./ToastProvider";

interface ClipCardProps {
  clip: Clip;
  selected: boolean;
  onToggleSelect: (clipId: string) => void;
  onOpenRerender: (clip: Clip) => void;
  onMetadataSaved: (clip: Clip) => void;
}

type MetaTab = "youtube" | "instagram" | "tiktok" | "hooks";

export function ClipCard({
  clip,
  selected,
  onToggleSelect,
  onOpenRerender,
  onMetadataSaved,
}: ClipCardProps) {
  const { toast } = useToast();
  const [tab, setTab] = useState<MetaTab>("youtube");
  const [menuOpen, setMenuOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [copiedKey, setCopiedKey] = useState<string | null>(null);

  const yt = clip.metadata.youtube_shorts || {};
  const ig = clip.metadata.instagram_reels || {};
  const tt = clip.metadata.tiktok || {};

  const [ytTitle, setYtTitle] = useState(yt.title || "");
  const [ytDesc, setYtDesc] = useState(yt.description || "");
  const [ytTags, setYtTags] = useState((yt.hashtags || []).join(" "));

  const [igCaption, setIgCaption] = useState(ig.caption || "");
  const [igTags, setIgTags] = useState((ig.hashtags || []).join(" "));

  const [ttCaption, setTtCaption] = useState(tt.caption || "");
  const [onScreenHook, setOnScreenHook] = useState(clip.metadata.on_screen_hook || "");

  const dirty =
    ytTitle !== (yt.title || "") ||
    ytDesc !== (yt.description || "") ||
    ytTags !== (yt.hashtags || []).join(" ") ||
    igCaption !== (ig.caption || "") ||
    igTags !== (ig.hashtags || []).join(" ") ||
    ttCaption !== (tt.caption || "") ||
    onScreenHook !== (clip.metadata.on_screen_hook || "");

  const copyText = async (key: string, text: string, label: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopiedKey(key);
      toast(`Copied ${label} to clipboard`, "success");
      setTimeout(() => setCopiedKey(null), 1800);
    } catch {
      toast("Clipboard permission denied", "error");
    }
  };

  const handleSaveMetadata = async () => {
    setSaving(true);
    try {
      const payload: ClipMetadata = {
        youtube_shorts: {
          title: ytTitle.slice(0, 100),
          description: ytDesc,
          hashtags: ytTags
            .split(/\s+/)
            .map((t) => t.trim())
            .filter(Boolean),
        },
        instagram_reels: {
          caption: igCaption,
          hashtags: igTags
            .split(/\s+/)
            .map((t) => t.trim())
            .filter(Boolean),
        },
        tiktok: {
          caption: ttCaption,
        },
        on_screen_hook: onScreenHook,
        alternative_titles: clip.metadata.alternative_titles || [],
      };
      const updated = await updateClipMetadata(clip.id, payload);
      onMetadataSaved(updated);
      toast(`Saved metadata for Clip #${clip.rank}`, "success");
    } catch (err) {
      toast(err instanceof Error ? err.message : "Failed to save metadata", "error");
    } finally {
      setSaving(false);
    }
  };

  const downloadItems: {
    type: "mp4" | "mp4_nocaptions" | "mp3" | "srt" | "thumb" | "meta";
    label: string;
    icon: React.ReactNode;
  }[] = [
    { type: "mp4", label: "MP4 (With Captions)", icon: <Film className="h-3.5 w-3.5" /> },
    { type: "mp4_nocaptions", label: "MP4 (No Captions)", icon: <Film className="h-3.5 w-3.5" /> },
    { type: "mp3", label: "MP3 Audio Only", icon: <Music className="h-3.5 w-3.5" /> },
    { type: "srt", label: "SRT Subtitles", icon: <Subtitles className="h-3.5 w-3.5" /> },
    { type: "thumb", label: "Thumbnail (JPG)", icon: <ImageIcon className="h-3.5 w-3.5" /> },
    { type: "meta", label: "Metadata (.txt)", icon: <FileText className="h-3.5 w-3.5" /> },
  ];

  return (
    <article
      className={`flex flex-col rounded-2xl border bg-[#11131b] transition-all ${
        selected
          ? "border-indigo-400/70 ring-1 ring-indigo-400/40 shadow-xl shadow-indigo-500/10"
          : "border-white/[0.08] hover:border-white/[0.15]"
      }`}
    >
      {/* Top Bar */}
      <div className="flex items-center justify-between gap-2 border-b border-white/[0.07] px-4 py-3">
        <label className="inline-flex items-center gap-2.5 cursor-pointer select-none">
          <input
            type="checkbox"
            checked={selected}
            onChange={() => onToggleSelect(clip.id)}
            aria-label={`Select Clip #${clip.rank} for bulk export`}
            className="h-4 w-4 rounded cursor-pointer"
          />
          <span className="inline-flex items-center rounded-md bg-indigo-500/15 px-2 py-0.5 text-xs font-bold text-indigo-300">
            #{clip.rank}
          </span>
          <span className="text-xs font-mono text-slate-400">
            {formatDuration(clip.start_sec)} – {formatDuration(clip.end_sec)}
          </span>
        </label>

        <div className="flex items-center gap-2 text-xs text-slate-400">
          <span className="rounded-md bg-white/[0.06] px-2 py-0.5 font-medium text-slate-200">
            {formatDuration(clip.duration_sec)}
          </span>
          <span className="rounded-md bg-white/[0.06] px-2 py-0.5 font-medium text-slate-300">
            {clip.output_format}
          </span>
        </div>
      </div>

      {/* Video Preview */}
      <div className="relative bg-black/90 flex items-center justify-center overflow-hidden">
        <video
          key={`${clip.id}-v${clip.render_version}`}
          src={clipDownloadUrl(clip.id, "mp4", true, clip.render_version)}
          poster={clipDownloadUrl(clip.id, "thumb", true, clip.render_version)}
          controls
          preload="metadata"
          playsInline
          className={`w-full object-contain ${
            clip.output_format === "9:16"
              ? "max-h-[430px] aspect-[9/16]"
              : clip.output_format === "1:1"
              ? "max-h-[360px] aspect-square"
              : "max-h-[280px] aspect-video"
          }`}
        />
      </div>

      {/* Score & Reason */}
      <div className="p-4 space-y-3.5 border-b border-white/[0.07]">
        <div className="flex items-start gap-3.5">
          <ScoreRing score={clip.final_score} size={52} />
          <div className="min-w-0 flex-1 space-y-1">
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="inline-flex items-center gap-1 rounded-full bg-violet-500/15 px-2 py-0.5 text-[11px] font-medium text-violet-300">
                <Sparkles className="h-3 w-3" />
                Hook: {clip.hook_quality}
              </span>
              {clip.standalone && (
                <span className="rounded-full bg-emerald-500/10 px-2 py-0.5 text-[11px] font-medium text-emerald-300">
                  Standalone
                </span>
              )}
            </div>
            <p className="text-xs leading-relaxed text-slate-300">{clip.llm_reason}</p>
          </div>
        </div>
        <ScoreBreakdown scores={clip.scores} />
      </div>

      {/* Editable Metadata Tabs */}
      <div className="p-4 flex-1 flex flex-col gap-3">
        <div className="flex items-center justify-between gap-2">
          <div className="inline-flex rounded-lg bg-[#090a0f] p-0.5 border border-white/[0.06]" role="tablist">
            {(
              [
                ["youtube", "Shorts"],
                ["instagram", "Reels"],
                ["tiktok", "TikTok"],
                ["hooks", "Hooks"],
              ] as [MetaTab, string][]
            ).map(([k, lbl]) => (
              <button
                key={k}
                type="button"
                role="tab"
                aria-selected={tab === k}
                onClick={() => setTab(k)}
                className={`rounded-md px-2.5 py-1 text-xs font-medium transition-colors ${
                  tab === k
                    ? "bg-white/12 text-white"
                    : "text-slate-400 hover:text-slate-200"
                }`}
              >
                {lbl}
              </button>
            ))}
          </div>

          {dirty && (
            <button
              type="button"
              onClick={handleSaveMetadata}
              disabled={saving}
              className="inline-flex items-center gap-1 rounded-lg bg-emerald-500/20 border border-emerald-400/30 px-2.5 py-1 text-xs font-medium text-emerald-300 hover:bg-emerald-500/30 transition-colors"
            >
              <Save className="h-3 w-3" />
              {saving ? "Saving..." : "Save"}
            </button>
          )}
        </div>

        {tab === "youtube" && (
          <div className="space-y-2.5">
            <div className="space-y-1">
              <div className="flex items-center justify-between">
                <label className="text-[11px] font-medium text-slate-400">
                  Title ({ytTitle.length}/100)
                </label>
                <button
                  type="button"
                  onClick={() => copyText("yt-title", ytTitle, "YouTube title")}
                  className="inline-flex items-center gap-1 text-[11px] text-indigo-300 hover:text-indigo-200"
                >
                  {copiedKey === "yt-title" ? <Check className="h-3 w-3" /> : <Copy className="h-3 w-3" />}
                  Copy
                </button>
              </div>
              <input
                type="text"
                maxLength={100}
                value={ytTitle}
                onChange={(e) => setYtTitle(e.target.value)}
                className="w-full rounded-lg border border-white/10 bg-[#0b0d14] px-2.5 py-1.5 text-xs text-white focus:border-indigo-400 focus:outline-none"
              />
            </div>

            <div className="space-y-1">
              <div className="flex items-center justify-between">
                <label className="text-[11px] font-medium text-slate-400">Description</label>
                <button
                  type="button"
                  onClick={() =>
                    copyText("yt-all", `${ytTitle}\n\n${ytDesc}\n\n${ytTags}`, "YouTube Shorts copy")
                  }
                  className="inline-flex items-center gap-1 text-[11px] text-indigo-300 hover:text-indigo-200"
                >
                  {copiedKey === "yt-all" ? <Check className="h-3 w-3" /> : <Copy className="h-3 w-3" />}
                  Copy All
                </button>
              </div>
              <textarea
                rows={3}
                value={ytDesc}
                onChange={(e) => setYtDesc(e.target.value)}
                className="w-full rounded-lg border border-white/10 bg-[#0b0d14] px-2.5 py-1.5 text-xs text-slate-200 focus:border-indigo-400 focus:outline-none resize-none"
              />
            </div>

            <input
              type="text"
              value={ytTags}
              onChange={(e) => setYtTags(e.target.value)}
              placeholder="#Shorts #topic"
              className="w-full rounded-lg border border-white/10 bg-[#0b0d14] px-2.5 py-1.5 text-xs text-indigo-300 focus:border-indigo-400 focus:outline-none"
            />
          </div>
        )}

        {tab === "instagram" && (
          <div className="space-y-2.5">
            <div className="space-y-1">
              <div className="flex items-center justify-between">
                <label className="text-[11px] font-medium text-slate-400">Reels Caption</label>
                <button
                  type="button"
                  onClick={() => copyText("ig-all", `${igCaption}\n\n${igTags}`, "Instagram Reels caption")}
                  className="inline-flex items-center gap-1 text-[11px] text-indigo-300 hover:text-indigo-200"
                >
                  {copiedKey === "ig-all" ? <Check className="h-3 w-3" /> : <Copy className="h-3 w-3" />}
                  Copy All
                </button>
              </div>
              <textarea
                rows={4}
                value={igCaption}
                onChange={(e) => setIgCaption(e.target.value)}
                className="w-full rounded-lg border border-white/10 bg-[#0b0d14] px-2.5 py-1.5 text-xs text-slate-200 focus:border-indigo-400 focus:outline-none resize-none"
              />
            </div>
            <input
              type="text"
              value={igTags}
              onChange={(e) => setIgTags(e.target.value)}
              placeholder="#reels #explore"
              className="w-full rounded-lg border border-white/10 bg-[#0b0d14] px-2.5 py-1.5 text-xs text-amber-300 focus:border-indigo-400 focus:outline-none"
            />
          </div>
        )}

        {tab === "tiktok" && (
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <label className="text-[11px] font-medium text-slate-400">TikTok Caption</label>
              <button
                type="button"
                onClick={() => copyText("tt", ttCaption, "TikTok caption")}
                className="inline-flex items-center gap-1 text-[11px] text-indigo-300 hover:text-indigo-200"
              >
                {copiedKey === "tt" ? <Check className="h-3 w-3" /> : <Copy className="h-3 w-3" />}
                Copy
              </button>
            </div>
            <textarea
              rows={4}
              value={ttCaption}
              onChange={(e) => setTtCaption(e.target.value)}
              className="w-full rounded-lg border border-white/10 bg-[#0b0d14] px-2.5 py-1.5 text-xs text-slate-200 focus:border-indigo-400 focus:outline-none resize-none"
            />
          </div>
        )}

        {tab === "hooks" && (
          <div className="space-y-2.5">
            <div className="space-y-1">
              <label className="text-[11px] font-medium text-slate-400">On-Screen Hook Text</label>
              <input
                type="text"
                value={onScreenHook}
                onChange={(e) => setOnScreenHook(e.target.value)}
                className="w-full rounded-lg border border-white/10 bg-[#0b0d14] px-2.5 py-1.5 text-xs font-semibold text-amber-300 focus:border-indigo-400 focus:outline-none"
              />
            </div>
            {(clip.metadata.alternative_titles || []).length > 0 && (
              <div className="space-y-1">
                <span className="text-[11px] font-medium text-slate-400">Alternative Titles (click to use)</span>
                <div className="space-y-1">
                  {(clip.metadata.alternative_titles || []).map((alt, i) => (
                    <button
                      key={i}
                      type="button"
                      onClick={() => {
                        setYtTitle(alt.slice(0, 100));
                        setTab("youtube");
                        toast("Applied alternative title — click Save to persist", "info");
                      }}
                      className="w-full text-left rounded-lg border border-white/[0.06] bg-[#0b0d14] px-2.5 py-1.5 text-xs text-slate-300 hover:border-indigo-400/40 hover:text-white transition-colors"
                    >
                      {alt}
                    </button>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
      </div>

      {/* Footer Actions */}
      <div className="flex items-center justify-between gap-2 border-t border-white/[0.07] px-4 py-3">
        <button
          type="button"
          onClick={() => onOpenRerender(clip)}
          className="inline-flex items-center gap-1.5 rounded-lg border border-white/10 bg-white/[0.04] px-3 py-1.5 text-xs font-medium text-slate-200 hover:bg-white/[0.09] transition-colors"
        >
          <Sliders className="h-3.5 w-3.5 text-indigo-400" />
          Trim / Re-render
        </button>

        <div className="relative">
          <button
            type="button"
            onClick={() => setMenuOpen((o) => !o)}
            aria-expanded={menuOpen}
            className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-500 px-3 py-1.5 text-xs font-semibold text-white hover:bg-indigo-400 transition-colors shadow-md shadow-indigo-500/20"
          >
            <Download className="h-3.5 w-3.5" />
            Download
            <ChevronDown className="h-3.5 w-3.5" />
          </button>

          {menuOpen && (
            <>
              <div
                className="fixed inset-0 z-10"
                onClick={() => setMenuOpen(false)}
                aria-hidden="true"
              />
              <div className="absolute right-0 bottom-full mb-2 z-20 w-52 rounded-xl border border-white/12 bg-[#161925] p-1.5 shadow-2xl">
                {downloadItems.map((item) => (
                  <a
                    key={item.type}
                    href={clipDownloadUrl(clip.id, item.type, false, clip.render_version)}
                    download
                    onClick={() => setMenuOpen(false)}
                    className="flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-xs text-slate-200 hover:bg-white/10 hover:text-white transition-colors"
                  >
                    <span className="text-indigo-400">{item.icon}</span>
                    <span>{item.label}</span>
                  </a>
                ))}
              </div>
            </>
          )}
        </div>
      </div>
    </article>
  );
}
