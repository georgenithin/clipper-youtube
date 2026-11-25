"use client";

import type { ClipScores } from "@/lib/types";

interface ScoreRingProps {
  score: number;
  size?: number;
}

export function ScoreRing({ score, size = 56 }: ScoreRingProps) {
  const clamped = Math.max(0, Math.min(100, Math.round(score)));
  const stroke = 4.5;
  const r = (size - stroke) / 2;
  const circ = 2 * Math.PI * r;
  const offset = circ - (clamped / 100) * circ;

  const color =
    clamped >= 80
      ? "text-emerald-400"
      : clamped >= 65
      ? "text-indigo-400"
      : clamped >= 50
      ? "text-amber-400"
      : "text-slate-400";

  return (
    <div
      className="relative inline-flex items-center justify-center shrink-0"
      style={{ width: size, height: size }}
      aria-label={`Overall clip score ${clamped} out of 100`}
    >
      <svg width={size} height={size} className="-rotate-90">
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          stroke="currentColor"
          strokeWidth={stroke}
          className="text-white/10"
        />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          stroke="currentColor"
          strokeWidth={stroke}
          strokeDasharray={circ}
          strokeDashoffset={offset}
          strokeLinecap="round"
          className={color}
        />
      </svg>
      <span className="absolute text-sm font-bold tabular-nums text-white">
        {clamped}
      </span>
    </div>
  );
}

const SIGNALS: { key: keyof Omit<ClipScores, "final">; label: string; barColor: string }[] = [
  { key: "llm", label: "LLM", barColor: "bg-violet-400" },
  { key: "replay_heat", label: "Replay", barColor: "bg-amber-400" },
  { key: "audio_energy", label: "Audio", barColor: "bg-emerald-400" },
  { key: "hook", label: "Hook", barColor: "bg-sky-400" },
  { key: "pace", label: "Pace", barColor: "bg-rose-400" },
];

export function ScoreBreakdown({ scores }: { scores: ClipScores }) {
  return (
    <div className="grid grid-cols-5 gap-2">
      {SIGNALS.map(({ key, label, barColor }) => {
        const val = scores[key];
        const pct = val === null || val === undefined ? 0 : Math.max(0, Math.min(100, Math.round(val)));
        return (
          <div key={key} className="flex flex-col gap-1">
            <div className="flex items-center justify-between text-[11px]">
              <span className="text-slate-400">{label}</span>
              <span className="font-medium tabular-nums text-slate-200">
                {val === null || val === undefined ? "—" : pct}
              </span>
            </div>
            <div className="h-1.5 w-full overflow-hidden rounded-full bg-white/10">
              <div
                className={`h-full rounded-full transition-all ${val === null ? "bg-transparent" : barColor}`}
                style={{ width: `${pct}%` }}
              />
            </div>
          </div>
        );
      })}
    </div>
  );
}
