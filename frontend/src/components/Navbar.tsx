"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Scissors, Settings, Home } from "lucide-react";

export function Navbar() {
  const pathname = usePathname();

  return (
    <header className="sticky top-0 z-30 border-b border-white/[0.07] bg-[#090a0f]/80 backdrop-blur-xl">
      <div className="mx-auto flex h-15 max-w-7xl items-center justify-between px-4 sm:px-6">
        <Link
          href="/"
          className="flex items-center gap-2.5 group focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-indigo-400 rounded-lg"
        >
          <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-gradient-to-br from-indigo-500 via-violet-500 to-amber-400 text-white shadow-lg shadow-indigo-500/20">
            <Scissors className="h-4 w-4" />
          </span>
          <div className="flex flex-col leading-none">
            <span className="text-base font-semibold tracking-tight text-white">
              ClipForge
            </span>
            <span className="text-[10px] font-medium uppercase tracking-widest text-slate-400">
              Local Studio
            </span>
          </div>
        </Link>

        <nav aria-label="Primary navigation" className="flex items-center gap-1.5">
          <Link
            href="/"
            aria-current={pathname === "/" ? "page" : undefined}
            className={`inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm font-medium transition-colors ${
              pathname === "/"
                ? "bg-white/10 text-white"
                : "text-slate-400 hover:bg-white/[0.05] hover:text-white"
            }`}
          >
            <Home className="h-4 w-4" />
            <span>Studio</span>
          </Link>
          <Link
            href="/settings"
            aria-current={pathname === "/settings" ? "page" : undefined}
            className={`inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm font-medium transition-colors ${
              pathname === "/settings"
                ? "bg-white/10 text-white"
                : "text-slate-400 hover:bg-white/[0.05] hover:text-white"
            }`}
          >
            <Settings className="h-4 w-4" />
            <span>Settings</span>
          </Link>
        </nav>
      </div>
    </header>
  );
}
