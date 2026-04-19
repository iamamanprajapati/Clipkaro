"""Clip download / preview streaming endpoints."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlmodel import Session

from config import CLIPS_DIR
from db import Clip, get_session


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/clips", tags=["clips"])


def _resolve_clip(clip_id: str, session: Session) -> Clip:
    clip = session.get(Clip, clip_id)
    if clip is None:
        raise HTTPException(status_code=404, detail="Clip not found")
    path = CLIPS_DIR / clip.filename
    if not path.exists():
        raise HTTPException(status_code=410, detail="Clip file is missing on disk")
    return clip


@router.get("/{clip_id}/download")
def download_clip(clip_id: str, session: Session = Depends(get_session)) -> FileResponse:
    clip = _resolve_clip(clip_id, session)
    path = CLIPS_DIR / clip.filename
    return FileResponse(
        path=str(path),
        media_type="video/mp4",
        filename=clip.filename,
        headers={"Content-Disposition": f'attachment; filename="{clip.filename}"'},
    )


@router.get("/{clip_id}/preview")
def preview_clip(clip_id: str, session: Session = Depends(get_session)) -> FileResponse:
    clip = _resolve_clip(clip_id, session)
    path = CLIPS_DIR / clip.filename
    return FileResponse(
        path=str(path),
        media_type="video/mp4",
        headers={"Content-Disposition": f'inline; filename="{clip.filename}"'},
    )
