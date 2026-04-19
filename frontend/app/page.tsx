"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { VideoList } from "@/components/video-list";
import { api, type VideoSummary } from "@/lib/api";

export default function HomePage() {
  const [videos, setVideos] = useState<VideoSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const errorShown = useRef(false);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        const data = await api.listVideos();
        if (cancelled) return;
        setVideos(data);
        setError(null);
      } catch (err) {
        if (cancelled) return;
        const message = err instanceof Error ? err.message : "Failed to load videos";
        setError(message);
        if (!errorShown.current) {
          toast.error(message);
          errorShown.current = true;
        }
      }
    }

    load();
    const interval = setInterval(() => {
      if (!videos) {
        load();
        return;
      }
      const hasActive = videos.some(
        (v) => v.status === "processing" || v.status === "uploaded",
      );
      if (hasActive) load();
    }, 5000);

    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [videos]);

  return (
    <div className="space-y-8">
      <section className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div className="space-y-2">
          <h1 className="text-3xl font-semibold tracking-tight sm:text-4xl">
            Your library
          </h1>
          <p className="max-w-xl text-muted-foreground">
            Upload long Hindi or Hinglish videos and ClipKar will generate 5
            short, ready-to-post vertical clips with animated subtitles.
          </p>
        </div>
        <Button asChild size="lg">
          <Link href="/upload">New upload</Link>
        </Button>
      </section>

      {error && !videos && (
        <div className="rounded-lg border border-destructive/40 bg-destructive/10 p-4 text-sm text-destructive">
          <p className="font-medium">Couldn’t reach the backend.</p>
          <p className="mt-1 text-destructive/80">{error}</p>
          <p className="mt-2 text-xs text-destructive/80">
            Make sure <code>python main.py</code> is running on port 8000.
          </p>
        </div>
      )}

      {videos === null && !error ? (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-72 w-full" />
          ))}
        </div>
      ) : videos ? (
        <VideoList videos={videos} />
      ) : null}
    </div>
  );
}
