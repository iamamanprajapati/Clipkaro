export const API_BASE: string =
  process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export interface ClipResponse {
  id: string;
  sequence: number;
  start_sec: number;
  end_sec: number;
  duration_sec: number;
  hook_text: string;
}

export interface VideoSummary {
  id: string;
  title: string;
  status: "uploaded" | "processing" | "completed" | "failed" | string;
  progress_message: string | null;
  error_message: string | null;
  created_at: string;
  completed_at: string | null;
  duration_sec: number | null;
  language: string | null;
  clip_count: number;
}

export interface VideoDetail extends VideoSummary {
  clips: ClipResponse[];
}

export interface UploadResponse {
  video_id: string;
  status: string;
}

async function parseError(response: Response): Promise<string> {
  try {
    const data = (await response.json()) as { detail?: unknown };
    if (typeof data.detail === "string") return data.detail;
    if (data.detail) return JSON.stringify(data.detail);
  } catch {
    /* ignore */
  }
  return `Request failed (${response.status})`;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    cache: "no-store",
    headers: {
      Accept: "application/json",
      ...(init?.headers || {}),
    },
  });
  if (!response.ok) {
    const message = await parseError(response);
    throw new Error(message);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export const api = {
  listVideos: () => request<VideoSummary[]>("/videos"),
  getVideo: (id: string) => request<VideoDetail>(`/videos/${id}`),
  deleteVideo: (id: string) =>
    request<void>(`/videos/${id}`, { method: "DELETE" }),
  health: () => request<{ status: string }>("/health"),
};

export interface UploadProgress {
  loaded: number;
  total: number;
  percent: number;
}

export function uploadVideoWithProgress(
  file: File,
  onProgress?: (progress: UploadProgress) => void,
): Promise<UploadResponse> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const formData = new FormData();
    formData.append("file", file);

    xhr.open("POST", `${API_BASE}/videos/upload`, true);

    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable && onProgress) {
        onProgress({
          loaded: event.loaded,
          total: event.total,
          percent: Math.round((event.loaded / event.total) * 100),
        });
      }
    };

    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          resolve(JSON.parse(xhr.responseText) as UploadResponse);
        } catch {
          reject(new Error("Invalid server response"));
        }
      } else {
        let message = `Upload failed (${xhr.status})`;
        try {
          const parsed = JSON.parse(xhr.responseText) as { detail?: string };
          if (parsed?.detail) message = parsed.detail;
        } catch {
          /* ignore */
        }
        reject(new Error(message));
      }
    };

    xhr.onerror = () =>
      reject(new Error("Network error — is the backend running?"));

    xhr.send(formData);
  });
}

export function clipPreviewUrl(clipId: string): string {
  return `${API_BASE}/clips/${clipId}/preview`;
}

export function clipDownloadUrl(clipId: string): string {
  return `${API_BASE}/clips/${clipId}/download`;
}
