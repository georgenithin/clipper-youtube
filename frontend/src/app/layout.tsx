import type { Metadata, Viewport } from "next";
import { Inter } from "next/font/google";
import "./globals.css";
import { Navbar } from "@/components/Navbar";
import { ToastProvider } from "@/components/ToastProvider";

const inter = Inter({ subsets: ["latin"], variable: "--font-inter" });

export const metadata: Metadata = {
  title: "ClipForge — Local Short-Form Clip Studio",
  description:
    "Paste a YouTube link to automatically find the best moments, cut vertical clips with word-highlighted captions, and generate Shorts & Reels metadata locally.",
};

export const viewport: Viewport = {
  colorScheme: "dark",
  themeColor: "#090a0f",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${inter.variable} dark`}>
      <body className="font-sans antialiased bg-[#090a0f] text-slate-100 selection:bg-indigo-500/35 selection:text-white">
        <ToastProvider>
          <Navbar />
          <main className="mx-auto max-w-7xl px-4 sm:px-6 pb-28 pt-8">
            {children}
          </main>
        </ToastProvider>
      </body>
    </html>
  );
}
