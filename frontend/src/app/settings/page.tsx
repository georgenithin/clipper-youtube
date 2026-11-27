"use client";

import React, { useEffect, useState } from "react";
import {
  CheckCircle2,
  Cpu,
  HardDrive,
  Instagram,
  KeyRound,
  Loader2,
  Save,
  Trash2,
  AlertCircle,
  Youtube,
} from "lucide-react";
import {
  cleanupStorage,
  disconnectYoutube,
  formatBytes,
  getPublishStatus,
  getSettings,
  saveInstagramConfig,
  updateSettings,
  youtubeConnectUrl,
} from "@/lib/api";
import type { PublishStatus, SettingsResponse } from "@/lib/types";
import { useToast } from "@/components/ToastProvider";

export default function SettingsPage() {
  const { toast } = useToast();
  const [settings, setSettings] = useState<SettingsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [cleaning, setCleaning] = useState(false);

  const [whisperModel, setWhisperModel] = useState("small");
  const [defaultFormat, setDefaultFormat] = useState("9:16");
  const [defaultCaptionStyle, setDefaultCaptionStyle] = useState("bold_yellow");
  const [autoDeleteDays, setAutoDeleteDays] = useState(30);

  const [pubStatus, setPubStatus] = useState<PublishStatus | null>(null);
  const [igUserId, setIgUserId] = useState("");
  const [igToken, setIgToken] = useState("");
  const [savingIg, setSavingIg] = useState(false);

  const load = async () => {
    try {
      const [data, pub] = await Promise.all([
        getSettings(),
        getPublishStatus().catch(() => null),
      ]);
      setSettings(data);
      setWhisperModel(data.whisper_model);
      setDefaultFormat(data.default_format);
      setDefaultCaptionStyle(data.default_caption_style);
      setAutoDeleteDays(data.auto_delete_days);
      if (pub) {
        setPubStatus(pub);
        if (pub.instagram.ig_user_id) setIgUserId(pub.instagram.ig_user_id);
      }
    } catch (err) {
      toast(err instanceof Error ? err.message : "Could not load settings", "error");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, []);

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaving(true);
    try {
      const updated = await updateSettings({
        whisper_model: whisperModel,
        default_format: defaultFormat,
        default_caption_style: defaultCaptionStyle,
        auto_delete_days: autoDeleteDays,
      });
      setSettings(updated);
      toast("Settings saved", "success");
    } catch (err) {
      toast(err instanceof Error ? err.message : "Failed to save settings", "error");
    } finally {
      setSaving(false);
    }
  };

  const handleCleanup = async (clearCache: boolean) => {
    setCleaning(true);
    try {
      const res = await cleanupStorage({
        older_than_days: clearCache ? 0 : autoDeleteDays,
        clear_cache: clearCache,
      });
      toast(
        `Cleaned up ${res.removed_jobs} jobs — freed ${formatBytes(res.freed_bytes)}`,
        "success"
      );
      await load();
    } catch (err) {
      toast(err instanceof Error ? err.message : "Cleanup failed", "error");
    } finally {
      setCleaning(false);
    }
  };

  if (loading || !settings) {
    return (
      <div className="max-w-3xl mx-auto space-y-4">
        {[1, 2, 3].map((n) => (
          <div key={n} className="h-32 animate-pulse rounded-2xl bg-[#11131b]" />
        ))}
      </div>
    );
  }

  return (
    <div className="max-w-3xl mx-auto space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-white">Settings</h1>
        <p className="text-sm text-slate-400">
          Configure local transcription, rendering defaults, Gemini AI, and disk storage.
        </p>
      </div>

      {/* Gemini & Toolchain Status */}
      <section className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <div className="rounded-2xl border border-white/[0.08] bg-[#11131b] p-5 space-y-2">
          <div className="flex items-center justify-between">
            <span className="inline-flex items-center gap-2 text-sm font-semibold text-white">
              <KeyRound className="h-4 w-4 text-indigo-400" />
              Gemini API Key
            </span>
            {settings.gemini_key_configured ? (
              <span className="inline-flex items-center gap-1 rounded-full bg-emerald-500/15 px-2.5 py-0.5 text-xs font-medium text-emerald-300">
                <CheckCircle2 className="h-3.5 w-3.5" />
                Configured
              </span>
            ) : (
              <span className="inline-flex items-center gap-1 rounded-full bg-amber-500/15 px-2.5 py-0.5 text-xs font-medium text-amber-300">
                <AlertCircle className="h-3.5 w-3.5" />
                Heuristic Fallback
              </span>
            )}
          </div>
          <p className="text-xs text-slate-400">
            Model: <code className="text-slate-200">{settings.gemini_model}</code>. Set{" "}
            <code className="text-slate-200">GEMINI_API_KEY</code> in <code className="text-slate-200">.env</code>{" "}
            and restart the backend to enable AI scoring &amp; copywriting.
          </p>
        </div>

        <div className="rounded-2xl border border-white/[0.08] bg-[#11131b] p-5 space-y-2">
          <div className="flex items-center justify-between">
            <span className="inline-flex items-center gap-2 text-sm font-semibold text-white">
              <Cpu className="h-4 w-4 text-violet-400" />
              Local Toolchain
            </span>
            <span className="rounded-full bg-white/10 px-2.5 py-0.5 text-xs font-mono text-indigo-300">
              {settings.dependencies.hw_encoder}
            </span>
          </div>
          <p className="text-xs text-slate-400 truncate">
            yt-dlp: <span className="text-slate-200">{settings.dependencies.yt_dlp || "missing"}</span>
          </p>
          <p className="text-xs text-slate-400 truncate">
            ffmpeg: <span className="text-slate-200">{settings.dependencies.ffmpeg ? "installed" : "missing"}</span>
          </p>
        </div>
      </section>

      {/* Defaults Form */}
      <form
        onSubmit={handleSave}
        className="rounded-2xl border border-white/[0.08] bg-[#11131b] p-6 space-y-5"
      >
        <h2 className="text-base font-semibold text-white">Pipeline Defaults</h2>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div className="space-y-1.5">
            <label htmlFor="set-whisper" className="block text-xs font-medium text-slate-300">
              Whisper Model Size
            </label>
            <select
              id="set-whisper"
              value={whisperModel}
              onChange={(e) => setWhisperModel(e.target.value)}
              className="w-full rounded-lg border border-white/10 bg-[#0b0d14] px-3 py-2 text-sm text-white"
            >
              <option value="tiny">tiny (fastest, lower accuracy)</option>
              <option value="base">base (fast)</option>
              <option value="small">small (recommended default)</option>
              <option value="medium">medium (higher accuracy, slower)</option>
              <option value="large-v3">large-v3 (best accuracy, GPU recommended)</option>
            </select>
          </div>

          <div className="space-y-1.5">
            <label htmlFor="set-format" className="block text-xs font-medium text-slate-300">
              Default Output Format
            </label>
            <select
              id="set-format"
              value={defaultFormat}
              onChange={(e) => setDefaultFormat(e.target.value)}
              className="w-full rounded-lg border border-white/10 bg-[#0b0d14] px-3 py-2 text-sm text-white"
            >
              <option value="9:16">9:16 Vertical (1080×1920)</option>
              <option value="1:1">1:1 Square (1080×1080)</option>
              <option value="16:9">16:9 Original</option>
            </select>
          </div>

          <div className="space-y-1.5">
            <label htmlFor="set-caption" className="block text-xs font-medium text-slate-300">
              Default Caption Style
            </label>
            <select
              id="set-caption"
              value={defaultCaptionStyle}
              onChange={(e) => setDefaultCaptionStyle(e.target.value)}
              className="w-full rounded-lg border border-white/10 bg-[#0b0d14] px-3 py-2 text-sm text-white"
            >
              <option value="bold_yellow">Bold Yellow Highlight</option>
              <option value="clean">Clean</option>
              <option value="minimal">Minimal</option>
            </select>
          </div>

          <div className="space-y-1.5">
            <label htmlFor="set-days" className="block text-xs font-medium text-slate-300">
              Auto-Clean Jobs Older Than (days)
            </label>
            <input
              id="set-days"
              type="number"
              min={0}
              max={365}
              value={autoDeleteDays}
              onChange={(e) => setAutoDeleteDays(Number(e.target.value))}
              className="w-full rounded-lg border border-white/10 bg-[#0b0d14] px-3 py-2 text-sm text-white"
            />
          </div>
        </div>

        <div className="flex justify-end">
          <button
            type="submit"
            disabled={saving}
            className="inline-flex items-center gap-2 rounded-xl bg-indigo-500 px-4 py-2 text-sm font-semibold text-white hover:bg-indigo-400 disabled:opacity-50"
          >
            {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
            Save Defaults
          </button>
        </div>
      </form>

      {/* Storage & Disk Usage */}
      <section className="rounded-2xl border border-white/[0.08] bg-[#11131b] p-6 space-y-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <HardDrive className="h-4 w-4 text-amber-400" />
            <h2 className="text-base font-semibold text-white">Local Storage</h2>
          </div>
          <span className="text-sm font-bold tabular-nums text-white">
            {formatBytes(settings.disk_usage.total_bytes)}
          </span>
        </div>

        <p className="text-xs font-mono text-slate-400 break-all">{settings.storage_path}</p>

        <div className="grid grid-cols-3 gap-3 text-xs">
          <div className="rounded-xl bg-[#0b0d14] border border-white/[0.06] p-3">
            <p className="text-slate-400">Downloads Cache</p>
            <p className="mt-1 text-sm font-semibold text-white">
              {formatBytes(settings.disk_usage.downloads_bytes)}
            </p>
          </div>
          <div className="rounded-xl bg-[#0b0d14] border border-white/[0.06] p-3">
            <p className="text-slate-400">Work / Transcripts</p>
            <p className="mt-1 text-sm font-semibold text-white">
              {formatBytes(settings.disk_usage.work_bytes)}
            </p>
          </div>
          <div className="rounded-xl bg-[#0b0d14] border border-white/[0.06] p-3">
            <p className="text-slate-400">Rendered Clips</p>
            <p className="mt-1 text-sm font-semibold text-white">
              {formatBytes(settings.disk_usage.outputs_bytes)}
            </p>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-3 pt-2">
          <button
            type="button"
            disabled={cleaning}
            onClick={() => handleCleanup(false)}
            className="inline-flex items-center gap-2 rounded-xl border border-white/12 bg-white/[0.05] px-4 py-2 text-xs font-semibold text-white hover:bg-white/10 disabled:opacity-50"
          >
            {cleaning ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Trash2 className="h-3.5 w-3.5" />}
            Clean Up Jobs Older Than {autoDeleteDays}d
          </button>
          <button
            type="button"
            disabled={cleaning}
            onClick={() => handleCleanup(true)}
            className="inline-flex items-center gap-2 rounded-xl border border-rose-500/30 bg-rose-500/10 px-4 py-2 text-xs font-semibold text-rose-300 hover:bg-rose-500/20 disabled:opacity-50"
          >
            <Trash2 className="h-3.5 w-3.5" />
            Clean Up All + Clear Cache
          </button>
        </div>
      </section>

      {/* Social Publishing Accounts */}
      <section className="rounded-2xl border border-white/[0.08] bg-[#11131b] p-6 space-y-5">
        <div>
          <h2 className="text-base font-semibold text-white">Social Publishing Accounts</h2>
          <p className="text-xs text-slate-400">
            Connect YouTube &amp; Instagram to post selected clips directly with their title, caption, and hashtags.
          </p>
        </div>

        {/* YouTube OAuth (Multi-Channel) */}
        <div className="rounded-xl border border-white/[0.07] bg-[#0b0d14] p-4 space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <Youtube className="h-4 w-4 text-rose-500" />
              <span className="text-sm font-semibold text-white">YouTube Channels</span>
              {pubStatus?.youtube.accounts && pubStatus.youtube.accounts.length > 0 && (
                <span className="rounded-full bg-emerald-500/15 px-2 py-0.5 text-[11px] font-medium text-emerald-300">
                  {pubStatus.youtube.accounts.length} connected
                </span>
              )}
            </div>
            {pubStatus?.youtube.oauth_configured ? (
              <a
                href={youtubeConnectUrl()}
                className="inline-flex items-center gap-1.5 rounded-lg bg-rose-500 px-3 py-1.5 text-xs font-semibold text-white hover:bg-rose-400"
              >
                <Youtube className="h-3.5 w-3.5" />
                {pubStatus.youtube.connected ? "+ Add Another Channel" : "Connect YouTube Account"}
              </a>
            ) : (
              <span className="rounded-full bg-amber-500/15 px-2.5 py-0.5 text-xs text-amber-300">
                Add YOUTUBE_CLIENT_ID &amp; SECRET in .env
              </span>
            )}
          </div>

          {pubStatus?.youtube.accounts && pubStatus.youtube.accounts.length > 0 && (
            <ul className="space-y-2 pt-1">
              {pubStatus.youtube.accounts.map((acc) => (
                <li
                  key={acc.id}
                  className="flex items-center justify-between gap-3 rounded-lg border border-white/[0.06] bg-[#121520] px-3 py-2"
                >
                  <div className="flex items-center gap-2.5 min-w-0">
                    {acc.thumbnail ? (
                      <img src={acc.thumbnail} alt="" className="h-6 w-6 rounded-full shrink-0" />
                    ) : (
                      <Youtube className="h-4 w-4 text-rose-400 shrink-0" />
                    )}
                    <span className="truncate text-xs font-medium text-white">{acc.channel_title}</span>
                  </div>
                  <button
                    type="button"
                    onClick={async () => {
                      await disconnectYoutube(acc.id);
                      toast(`Removed ${acc.channel_title}`, "info");
                      load();
                    }}
                    className="rounded-md border border-white/10 px-2 py-1 text-[11px] text-slate-300 hover:bg-rose-500/15 hover:text-rose-300 shrink-0"
                  >
                    Remove
                  </button>
                </li>
              ))}
            </ul>
          )}

          {pubStatus?.youtube.redirect_uri && (
            <p className="text-[11px] text-slate-500 break-all">
              Authorized Redirect URI:{" "}
              <code className="text-slate-300">{pubStatus.youtube.redirect_uri}</code>
            </p>
          )}
        </div>

        {/* Instagram Graph API */}
        <form
          onSubmit={async (e) => {
            e.preventDefault();
            if (!igUserId.trim() || !igToken.trim()) {
              toast("Enter both Instagram User ID and Access Token", "error");
              return;
            }
            setSavingIg(true);
            try {
              await saveInstagramConfig(igToken.trim(), igUserId.trim());
              setIgToken("");
              toast("Instagram Reels account saved!", "success");
              await load();
            } catch (err) {
              toast(err instanceof Error ? err.message : "Could not save Instagram config", "error");
            } finally {
              setSavingIg(false);
            }
          }}
          className="rounded-xl border border-white/[0.07] bg-[#0b0d14] p-4 space-y-3"
        >
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Instagram className="h-4 w-4 text-pink-400" />
              <span className="text-sm font-semibold text-white">Instagram Reels</span>
            </div>
            {pubStatus?.instagram.connected ? (
              <span className="rounded-full bg-emerald-500/15 px-2.5 py-0.5 text-xs font-medium text-emerald-300">
                Connected ({pubStatus.instagram.ig_user_id})
              </span>
            ) : (
              <span className="text-xs text-slate-500">Business / Creator account required</span>
            )}
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <input
              type="text"
              value={igUserId}
              onChange={(e) => setIgUserId(e.target.value)}
              placeholder="Instagram Account ID (numeric)"
              aria-label="Instagram Account ID"
              className="rounded-lg border border-white/10 bg-[#121520] px-3 py-2 text-xs text-white placeholder:text-slate-500"
            />
            <input
              type="password"
              value={igToken}
              onChange={(e) => setIgToken(e.target.value)}
              placeholder="Long-lived Meta Graph Access Token"
              aria-label="Instagram Access Token"
              className="rounded-lg border border-white/10 bg-[#121520] px-3 py-2 text-xs text-white placeholder:text-slate-500"
            />
          </div>
          <div className="flex justify-end">
            <button
              type="submit"
              disabled={savingIg}
              className="inline-flex items-center gap-1.5 rounded-lg bg-pink-500/20 border border-pink-400/30 px-3 py-1.5 text-xs font-semibold text-pink-200 hover:bg-pink-500/30 disabled:opacity-50"
            >
              {savingIg ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}
              Save Instagram Account
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}
