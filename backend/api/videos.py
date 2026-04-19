"""Video upload, listing, retrieval and deletion endpoints."""

from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import List, Optional
from uuid import uuid4

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    HTTPException,
    UploadFile,
    status,
)
from fastapi.responses import Response
from pydantic import BaseModel
from sqlmodel import Session, select

from config import UPLOADS_DIR, settings
from db import Clip, Video, get_session
from services.pipeline import cleanup_video_files, run_in_background


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/videos", tags=["videos"])


class ClipResponse(BaseModel):
    id: str
    sequence: int
    start_sec: float
    end_sec: float
    duration_sec: float
    hook_text: str


class VideoSummary(BaseModel):
    id: str
    title: str
    status: str
    progress_message: Optional[str]
    error_message: Optional[str]
    created_at: datetime
    completed_at: Optional[datetime]
    duration_sec: Optional[float]
    language: Optional[str]
    clip_count: int


class VideoDetail(VideoSummary):
    clips: List[ClipResponse]


class UploadResponse(BaseModel):
    video_id: str
    status: str


def _safe_filename(name: str) -> str:
    """Strip path components and dangerous chars from a user-supplied filename."""
    base = Path(name).name
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", base)
    return base or "upload"


def _extension_allowed(filename: str) -> bool:
    return Path(filename).suffix.lower() in settings.ALLOWED_EXTENSIONS


@router.post("/upload", response_model=UploadResponse)
async def upload_video(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
) -> UploadResponse:
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided")

    if not _extension_allowed(file.filename):
        raise HTTPException(
            status_code=400,
            detail=(
                "Unsupported file type. Allowed: "
                + ", ".join(settings.ALLOWED_EXTENSIONS)
            ),
        )

    video_id = str(uuid4())
    safe_name = _safe_filename(file.filename)
    stored_filename = f"{video_id}_{safe_name}"
    destination = UPLOADS_DIR / stored_filename

    bytes_written = 0
    chunk_size = 1024 * 1024
    try:
        with destination.open("wb") as out:
            while True:
                chunk = await file.read(chunk_size)
                if not chunk:
                    break
                bytes_written += len(chunk)
                if bytes_written > settings.max_upload_bytes:
                    out.close()
                    destination.unlink(missing_ok=True)
                    raise HTTPException(
                        status_code=413,
                        detail=f"File exceeds max upload size of {settings.MAX_UPLOAD_MB} MB",
                    )
                out.write(chunk)
    except HTTPException:
        raise
    except Exception as exc:
        destination.unlink(missing_ok=True)
        logger.exception("Failed to save uploaded file")
        raise HTTPException(status_code=500, detail=f"Failed to save upload: {exc}") from exc
    finally:
        await file.close()

    title = Path(file.filename).stem or "Untitled"
    video = Video(
        id=video_id,
        title=title,
        filename=stored_filename,
        status="uploaded",
        progress_message="Queued for processing...",
    )
    session.add(video)
    session.commit()

    background_tasks.add_task(run_in_background, video_id)

    return UploadResponse(video_id=video_id, status="processing")


def _clip_count(session: Session, video_id: str) -> int:
    return len(session.exec(select(Clip).where(Clip.video_id == video_id)).all())


def _summary(session: Session, video: Video) -> VideoSummary:
    return VideoSummary(
        id=video.id,
        title=video.title,
        status=video.status,
        progress_message=video.progress_message,
        error_message=video.error_message,
        created_at=video.created_at,
        completed_at=video.completed_at,
        duration_sec=video.duration_sec,
        language=video.language,
        clip_count=_clip_count(session, video.id),
    )


@router.get("", response_model=List[VideoSummary])
def list_videos(session: Session = Depends(get_session)) -> List[VideoSummary]:
    rows = session.exec(select(Video).order_by(Video.created_at.desc())).all()
    return [_summary(session, v) for v in rows]


@router.get("/{video_id}", response_model=VideoDetail)
def get_video(video_id: str, session: Session = Depends(get_session)) -> VideoDetail:
    video = session.get(Video, video_id)
    if video is None:
        raise HTTPException(status_code=404, detail="Video not found")

    clips = session.exec(
        select(Clip).where(Clip.video_id == video_id).order_by(Clip.sequence)
    ).all()

    summary = _summary(session, video)
    return VideoDetail(
        **summary.model_dump(),
        clips=[
            ClipResponse(
                id=c.id,
                sequence=c.sequence,
                start_sec=c.start_sec,
                end_sec=c.end_sec,
                duration_sec=c.duration_sec,
                hook_text=c.hook_text,
            )
            for c in clips
        ],
    )


@router.delete(
    "/{video_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def delete_video(
    video_id: str, session: Session = Depends(get_session)
) -> Response:
    video = session.get(Video, video_id)
    if video is None:
        raise HTTPException(status_code=404, detail="Video not found")

    clips = session.exec(select(Clip).where(Clip.video_id == video_id)).all()
    clip_filenames = [c.filename for c in clips]
    video_filename = video.filename

    for c in clips:
        session.delete(c)
    session.delete(video)
    session.commit()

    cleanup_video_files(video_id, video_filename, clip_filenames)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
