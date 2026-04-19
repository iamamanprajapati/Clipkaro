"use client";

import { Download } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardFooter, CardHeader } from "@/components/ui/card";
import {
  type ClipResponse,
  clipDownloadUrl,
  clipPreviewUrl,
} from "@/lib/api";
import { formatDuration, formatTimestamp } from "@/lib/utils";

interface ClipCardProps {
  clip: ClipResponse;
}

export function ClipCard({ clip }: ClipCardProps) {
  return (
    <Card className="flex flex-col overflow-hidden">
      <CardHeader className="space-y-2 pb-3">
        <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          Clip {clip.sequence}
        </p>
        <h3 className="line-clamp-2 text-lg font-semibold leading-snug">
          {clip.hook_text}
        </h3>
      </CardHeader>
      <CardContent className="flex-1 space-y-3 pb-3">
        <div className="aspect-[9/16] w-full overflow-hidden rounded-lg bg-black">
          <video
            controls
            preload="metadata"
            className="h-full w-full"
            src={clipPreviewUrl(clip.id)}
          />
        </div>
        <div className="flex items-center justify-between text-xs text-muted-foreground">
          <span>Starts at {formatTimestamp(clip.start_sec)}</span>
          <span>{formatDuration(clip.duration_sec)}</span>
        </div>
      </CardContent>
      <CardFooter>
        <Button asChild className="w-full" size="sm">
          <a href={clipDownloadUrl(clip.id)} download>
            <Download className="mr-2 h-4 w-4" />
            Download
          </a>
        </Button>
      </CardFooter>
    </Card>
  );
}
