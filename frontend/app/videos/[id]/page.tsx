"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { ArrowLeft, Loader2, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Progress } from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import { ClipCard } from "@/components/clip-card";
import { StatusBadge } from "@/components/status-badge";
import { api, type VideoDetail } from "@/lib/api";
import { formatDate, formatDuration } from "@/lib/utils";

const POLL_INTERVAL_MS = 3000;

function inferProgressPercent(detail: VideoDetail): number {
  if (detail.status === "completed") return 100;
  if (detail.status === "failed") return 0;
  const message = (detail.progress_message || "").toLowerCase();
  if (message.startsWith("extracting")) return 5;
  if (message.startsWith("transcribing")) return 25;
  if (message.startsWith("finding")) return 45;
  const renderMatch = message.match(/rendering clip (\d+)\/(\d+)/);
  if (renderMatch) {
    const current = Number(renderMatch[1]);
    const total = Number(renderMatch[2]) || 5;
    return Math.min(95, 50 + Math.round(((current - 1) / total) * 45));
  }
  if (message === "done") return 100;
  return 10;
}

export default function VideoDetailPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const id = params?.id;

  const [video, setVideo] = useState<VideoDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const errorShown = useRef(false);

  useEffect(() => {
    if (!id) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    async function tick() {
      try {
        const data = await api.getVideo(id as string);
        if (cancelled) return;
        setVideo(data);
        setError(null);
        if (data.status === "processing" || data.status === "uploaded") {
          timer = setTimeout(tick, POLL_INTERVAL_MS);
        }
      } catch (err) {
        if (cancelled) return;
        const message = err instanceof Error ? err.message : "Failed to load video";
        setError(message);
        if (!errorShown.current) {
          toast.error(message);
          errorShown.current = true;
        }
        timer = setTimeout(tick, POLL_INTERVAL_MS * 2);
      }
    }

    tick();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [id]);

  const handleDelete = async () => {
    if (!id || deleting) return;
    setDeleting(true);
    try {
      await api.deleteVideo(id as string);
      toast.success("Video deleted");
      router.push("/");
    } catch (err) {
      const message = err instanceof Error ? err.message : "Failed to delete video";
      toast.error(message);
      setDeleting(false);
    }
  };

  if (!id) return null;

  if (!video && !error) {
    return (
      <div className="space-y-6">
        <Skeleton className="h-8 w-1/3" />
        <Skeleton className="h-32 w-full" />
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-96 w-full" />
          ))}
        </div>
      </div>
    );
  }

  if (!video) {
    return (
      <div className="space-y-4">
        <Button asChild variant="ghost" size="sm">
          <Link href="/">
            <ArrowLeft className="mr-2 h-4 w-4" /> Back
          </Link>
        </Button>
        <Card>
          <CardContent className="space-y-3 p-6">
            <p className="font-medium">Couldn’t load this video.</p>
            <p className="text-sm text-muted-foreground">{error}</p>
          </CardContent>
        </Card>
      </div>
    );
  }

  const percent = inferProgressPercent(video);

  return (
    <div className="space-y-8">
      <div className="space-y-4">
        <Button asChild variant="ghost" size="sm" className="-ml-3">
          <Link href="/">
            <ArrowLeft className="mr-2 h-4 w-4" /> Back to library
          </Link>
        </Button>
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div className="space-y-2">
            <div className="flex flex-wrap items-center gap-3">
              <h1 className="text-3xl font-semibold tracking-tight">
                {video.title}
              </h1>
              <StatusBadge status={video.status} />
            </div>
            <p className="text-sm text-muted-foreground">
              Uploaded {formatDate(video.created_at)}
              {video.duration_sec
                ? ` • ${formatDuration(video.duration_sec)}`
                : ""}
              {video.language ? ` • Language: ${video.language}` : ""}
            </p>
          </div>
        </div>
      </div>

      {video.status === "processing" || video.status === "uploaded" ? (
        <Card>
          <CardContent className="space-y-4 p-6">
            <div className="flex items-center gap-3 text-sm font-medium">
              <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
              <span>{video.progress_message || "Working…"}</span>
            </div>
            <Progress value={percent} />
            <p className="text-xs text-muted-foreground">
              Processing a 20-minute video typically takes 3–5 minutes. This
              page refreshes automatically.
            </p>
          </CardContent>
        </Card>
      ) : null}

      {video.status === "failed" ? (
        <Card className="border-destructive/40 bg-destructive/5">
          <CardContent className="space-y-3 p-6">
            <p className="font-semibold text-destructive">Processing failed</p>
            <p className="text-sm text-destructive/90">
              {video.error_message || "Unknown error"}
            </p>
            <p className="text-xs text-muted-foreground">
              Delete this video and re-upload it to retry. The error is also
              logged in the backend terminal.
            </p>
          </CardContent>
        </Card>
      ) : null}

      {video.status === "completed" ? (
        video.clips.length > 0 ? (
          <div className="grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
            {video.clips.map((clip) => (
              <ClipCard key={clip.id} clip={clip} />
            ))}
          </div>
        ) : (
          <Card>
            <CardContent className="p-6 text-sm text-muted-foreground">
              No clips were generated. Try uploading again.
            </CardContent>
          </Card>
        )
      ) : null}

      <div className="border-t pt-6">
        <Dialog open={deleteOpen} onOpenChange={setDeleteOpen}>
          <DialogTrigger asChild>
            <Button variant="destructive" size="sm">
              <Trash2 className="mr-2 h-4 w-4" />
              Delete video
            </Button>
          </DialogTrigger>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Delete this video?</DialogTitle>
              <DialogDescription>
                This permanently removes the original upload and all generated
                clips from your laptop. This cannot be undone.
              </DialogDescription>
            </DialogHeader>
            <DialogFooter>
              <Button
                variant="outline"
                onClick={() => setDeleteOpen(false)}
                disabled={deleting}
              >
                Cancel
              </Button>
              <Button
                variant="destructive"
                onClick={handleDelete}
                disabled={deleting}
              >
                {deleting ? "Deleting…" : "Delete forever"}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </div>
    </div>
  );
}
