"""OpenAI Whisper transcription with word- and segment-level timestamps."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, List, Optional

from openai import OpenAI

from config import WHISPER_CACHE_DIR, settings
from services import api_cache


logger = logging.getLogger(__name__)


# Bumped to v2: cache key is now the SOURCE VIDEO hash (not the audio).
# Re-extracting audio with ffmpeg is non-deterministic (MP3 metadata),
# so hashing the audio caused a fresh API call on every re-upload.
CACHE_VERSION = "v2"


@dataclass
class WordTimestamp:
    word: str
    start: float
    end: float


@dataclass
class Segment:
    start: float
    end: float
    text: str


@dataclass
class Transcript:
    language: Optional[str]
    duration: Optional[float]
    text: str
    words: List[WordTimestamp] = field(default_factory=list)
    segments: List[Segment] = field(default_factory=list)


def _client() -> OpenAI:
    if not settings.OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not configured. Add it to backend/.env")
    return OpenAI(api_key=settings.OPENAI_API_KEY)


def _cache_path_for_hash(content_hash: str) -> Path:
    """File path where the cached Whisper response for this content lives."""
    key = api_cache.hash_text(
        f"{CACHE_VERSION}|{settings.WHISPER_MODEL}|{content_hash}"
    )
    return WHISPER_CACHE_DIR / f"{key}.json"


def _parse_raw(raw: dict) -> Transcript:
    """Convert a raw verbose-JSON Whisper response into a `Transcript`."""
    words: List[WordTimestamp] = []
    for w in raw.get("words", []) or []:
        try:
            words.append(
                WordTimestamp(
                    word=str(w.get("word", "")).strip(),
                    start=float(w.get("start", 0.0)),
                    end=float(w.get("end", 0.0)),
                )
            )
        except (TypeError, ValueError):
            continue

    segments: List[Segment] = []
    for s in raw.get("segments", []) or []:
        try:
            segments.append(
                Segment(
                    start=float(s.get("start", 0.0)),
                    end=float(s.get("end", 0.0)),
                    text=str(s.get("text", "")).strip(),
                )
            )
        except (TypeError, ValueError):
            continue

    return Transcript(
        language=raw.get("language"),
        duration=raw.get("duration"),
        text=str(raw.get("text", "")),
        words=words,
        segments=segments,
    )


def _call_whisper(audio_path: Path) -> dict:
    """Actually call the OpenAI Whisper API and return the raw response dict."""
    client = _client()
    with audio_path.open("rb") as fp:
        response: Any = client.audio.transcriptions.create(
            model=settings.WHISPER_MODEL,
            file=fp,
            response_format="verbose_json",
            timestamp_granularities=["word", "segment"],
        )
    return response.model_dump() if hasattr(response, "model_dump") else dict(response)


def transcribe(audio_path: Path, *, content_hash: Optional[str] = None) -> Transcript:
    """Transcribe an audio file via Whisper.

    Caching is keyed by `content_hash` if supplied (recommended: pass the
    SHA-256 of the source video). If not supplied we fall back to hashing
    the audio file itself, which is fine for one-off use but not stable
    across repeated audio extractions of the same source.
    """

    if not audio_path.exists():
        raise FileNotFoundError(f"Audio file not found: {audio_path}")

    if content_hash is None:
        content_hash = api_cache.hash_file(audio_path)

    cache_path = _cache_path_for_hash(content_hash)
    cached = api_cache.load_json(cache_path)
    if cached is not None:
        logger.info(
            "Whisper cache HIT (%s) — skipping OpenAI call.", cache_path.name
        )
        raw = cached
    else:
        logger.info("Whisper cache MISS — calling OpenAI for %s", audio_path.name)
        raw = _call_whisper(audio_path)
        api_cache.save_json(cache_path, raw)

    transcript = _parse_raw(raw)
    logger.info(
        "Whisper returned %d words, %d segments, language=%s",
        len(transcript.words),
        len(transcript.segments),
        transcript.language,
    )
    return transcript


def transcribe_video(
    video_path: Path,
    audio_path: Path,
    extract_audio_fn: Callable[[Path, Path], Path],
) -> Transcript:
    """Transcribe a video, caching by the SOURCE VIDEO's SHA-256.

    On a cache hit we skip BOTH the audio extraction and the OpenAI call —
    the cached transcript is returned immediately. On a cache miss the
    given `extract_audio_fn(video_path, audio_path)` is invoked to produce
    the audio, which is then sent to Whisper and cached.
    """

    if not video_path.exists():
        raise FileNotFoundError(f"Video file not found: {video_path}")

    video_hash = api_cache.hash_file(video_path)
    cache_path = _cache_path_for_hash(video_hash)
    cached = api_cache.load_json(cache_path)
    if cached is not None:
        logger.info(
            "Whisper cache HIT for video %s (%s) — skipping audio extract + OpenAI call.",
            video_path.name,
            cache_path.name,
        )
        transcript = _parse_raw(cached)
        logger.info(
            "Whisper (cached) returned %d words, %d segments, language=%s",
            len(transcript.words),
            len(transcript.segments),
            transcript.language,
        )
        return transcript

    if not audio_path.exists():
        extract_audio_fn(video_path, audio_path)

    logger.info(
        "Whisper cache MISS for video %s — calling OpenAI.", video_path.name
    )
    raw = _call_whisper(audio_path)
    api_cache.save_json(cache_path, raw)

    transcript = _parse_raw(raw)
    logger.info(
        "Whisper returned %d words, %d segments, language=%s",
        len(transcript.words),
        len(transcript.segments),
        transcript.language,
    )
    return transcript
