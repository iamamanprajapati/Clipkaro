"use client";

import Link from "next/link";
import { Film } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { StatusBadge } from "@/components/status-badge";
import type { VideoSummary } from "@/lib/api";
import { formatDate } from "@/lib/utils";

interface VideoListProps {
  videos: VideoSummary[];
}

export function VideoList({ videos }: VideoListProps) {
  if (videos.length === 0) {
    return (
      <Card className="border-dashed">
        <CardContent className="flex flex-col items-center justify-center gap-4 py-16 text-center">
          <div className="rounded-full bg-secondary p-4">
            <Film className="h-8 w-8 text-muted-foreground" />
          </div>
          <div className="space-y-1">
            <p className="text-base font-medium">No videos yet</p>
            <p className="text-sm text-muted-foreground">
              Upload your first long-form video to generate 5 short clips.
            </p>
          </div>
          <Button asChild>
            <Link href="/upload">New upload</Link>
          </Button>
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
      {videos.map((video) => (
        <Card key={video.id} className="flex flex-col overflow-hidden">
          <div className="flex aspect-video items-center justify-center bg-gradient-to-br from-secondary to-muted text-muted-foreground">
            <Film className="h-10 w-10" />
          </div>
          <CardHeader className="pb-3">
            <div className="flex items-start justify-between gap-2">
              <CardTitle className="line-clamp-2 text-base">
                {video.title}
              </CardTitle>
              <StatusBadge status={video.status} />
            </div>
            <p className="text-xs text-muted-foreground">
              {formatDate(video.created_at)}
            </p>
          </CardHeader>
          <CardContent className="flex-1 pb-3 text-sm text-muted-foreground">
            {video.status === "processing" && video.progress_message ? (
              <p>{video.progress_message}</p>
            ) : video.status === "failed" ? (
              <p className="text-destructive">
                {video.error_message || "Processing failed"}
              </p>
            ) : video.status === "completed" ? (
              <p>{video.clip_count} clips ready</p>
            ) : (
              <p>Queued for processing</p>
            )}
          </CardContent>
          <CardFooter>
            <Button asChild variant="outline" size="sm" className="w-full">
              <Link href={`/videos/${video.id}`}>
                {video.status === "completed" ? "View clips" : "Open"}
              </Link>
            </Button>
          </CardFooter>
        </Card>
      ))}
    </div>
  );
}
