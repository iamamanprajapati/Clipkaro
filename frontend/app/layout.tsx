import type { Metadata } from "next";
import Link from "next/link";
import { Toaster } from "sonner";

import { Button } from "@/components/ui/button";
import "./globals.css";

export const metadata: Metadata = {
  title: "ClipKar — AI shorts for Indian creators",
  description:
    "Turn long Hindi/Hinglish videos into 5 vertical short clips with animated subtitles.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body className="min-h-screen bg-background">
        <header className="border-b border-border/60 bg-background/80 backdrop-blur">
          <div className="container flex h-16 items-center justify-between">
            <Link href="/" className="flex items-center gap-2">
              <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary font-bold text-primary-foreground">
                C
              </span>
              <span className="text-lg font-semibold tracking-tight">
                ClipKar
              </span>
            </Link>
            <nav className="flex items-center gap-2">
              <Button asChild variant="ghost" size="sm">
                <Link href="/">Library</Link>
              </Button>
              <Button asChild size="sm">
                <Link href="/upload">New upload</Link>
              </Button>
            </nav>
          </div>
        </header>
        <main className="container py-10">{children}</main>
        <Toaster position="top-right" richColors closeButton />
      </body>
    </html>
  );
}
