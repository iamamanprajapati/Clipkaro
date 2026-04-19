"""End-to-end pipeline that turns one uploaded video into 5 short clips."""

from __future__ import annotations

import logging
import traceback
from datetime import datetime
from pathlib import Path
from typing import List

from sqlmodel import select

from config import CLIPS_DIR, TEMP_DIR, UPLOADS_DIR
from db import Clip, Video, session_scope
from services import claude_analyze, renderer, whisper


logger = logging.getLogger(__name__)


def _set_progress(video_id: str, message: str) -> None:
    with session_scope() as session:
        video = session.get(Video, video_id)
        if video is None:
            return
        video.progress_message = message
        session.add(video)
    logger.info("[%s] %s", video_id, message)


def _set_status(
    video_id: str,
    status: str,
    *,
    error_message: str | None = None,
    completed: bool = False,
    language: str | None = None,
    duration: float | None = None,
) -> None:
    with session_scope() as session:
        video = session.get(Video, video_id)
        if video is None:
            return
        video.status = status
        if error_message is not None:
            video.error_message = error_message
        if completed:
            video.completed_at = datetime.utcnow()
        if language is not None:
            video.language = language
        if duration is not None:
            video.duration_sec = duration
        session.add(video)


def _existing_clip_sequences(video_id: str) -> set[int]:
    with session_scope() as session:
        rows = session.exec(select(Clip).where(Clip.video_id == video_id)).all()
        return {row.sequence for row in rows}


def _save_clip_row(
    video_id: str,
    sequence: int,
    pick: claude_analyze.ClipPick,
    filename: str,
) -> None:
    with session_scope() as session:
        clip = Clip(
            video_id=video_id,
            sequence=sequence,
            start_sec=pick.start_sec,
            end_sec=pick.end_sec,
            hook_text=pick.hook,
            filename=filename,
            duration_sec=max(0.0, pick.end_sec - pick.start_sec),
        )
        session.add(clip)


def process_video(video_id: str) -> None:
    """Background entrypoint. Always swallows exceptions and writes status."""

    logger.info("=== Starting pipeline for video %s ===", video_id)

    try:
        with session_scope() as session:
            video = session.get(Video, video_id)
            if video is None:
                logger.error("Video %s not found in database", video_id)
                return
            source_filename = video.filename
            video.status = "processing"
            video.error_message = None
            video.progress_message = "Extracting audio..."
            session.add(video)

        source_path = UPLOADS_DIR / source_filename
        if not source_path.exists():
            raise FileNotFoundError(f"Uploaded file missing: {source_path}")

        renderer.ensure_ffmpeg()

        duration = renderer.probe_duration(source_path)
        if duration is not None:
            _set_status(video_id, "processing", duration=duration)

        audio_path = TEMP_DIR / f"{video_id}.mp3"

        _set_progress(video_id, "Transcribing with Whisper...")
        # Cached on the source video's SHA-256: re-uploads of the same
        # file skip both audio extraction and the OpenAI call.
        transcript = whisper.transcribe_video(
            source_path,
            audio_path,
            extract_audio_fn=renderer.extract_audio,
        )
        if transcript.language:
            _set_status(video_id, "processing", language=transcript.language)

        _set_progress(video_id, "Finding best moments...")
        picks: List[claude_analyze.ClipPick] = claude_analyze.pick_clips(transcript)

        already_done = _existing_clip_sequences(video_id)

        for index, pick in enumerate(picks, start=1):
            if index in already_done:
                logger.info("Skipping clip %d (already rendered)", index)
                continue

            _set_progress(
                video_id,
                f'Rendering clip {index}/{len(picks)}: "{pick.hook}"...',
            )

            output_filename = f"{video_id}_clip_{index}.mp4"
            output_path = CLIPS_DIR / output_filename
            ass_path = TEMP_DIR / f"{video_id}_clip_{index}.ass"

            req = renderer.RenderRequest(
                source_path=source_path,
                output_path=output_path,
                start_sec=pick.start_sec,
                end_sec=pick.end_sec,
                hook_text=pick.hook,
                words=transcript.words,
                ass_path=ass_path,
            )
            renderer.render_clip(req)
            _save_clip_row(video_id, index, pick, output_filename)

            try:
                ass_path.unlink(missing_ok=True)
            except OSError:
                pass

        _set_status(
            video_id,
            "completed",
            completed=True,
        )
        _set_progress(video_id, "Done")

        try:
            audio_path.unlink(missing_ok=True)
        except OSError:
            pass

        logger.info("=== Finished pipeline for video %s ===", video_id)

    except Exception as exc:
        traceback.print_exc()
        message = str(exc) or exc.__class__.__name__
        logger.error("Pipeline failed for %s: %s", video_id, message)
        _set_status(
            video_id,
            "failed",
            error_message=message,
        )
        _set_progress(video_id, "Failed")


def run_in_background(video_id: str) -> None:
    """Wrapper used by FastAPI BackgroundTasks."""
    process_video(video_id)


def cleanup_video_files(video_id: str, video_filename: str, clip_filenames: List[str]) -> None:
    """Delete all on-disk artifacts for a video."""
    candidates: list[Path] = [
        UPLOADS_DIR / video_filename,
        TEMP_DIR / f"{video_id}.mp3",
    ]
    for clip_filename in clip_filenames:
        candidates.append(CLIPS_DIR / clip_filename)
    for path in candidates:
        try:
            if path.exists():
                path.unlink()
        except OSError as exc:
            logger.warning("Failed to delete %s: %s", path, exc)
