"""OpenAI Whisper transcription with word- and segment-level timestamps."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Optional

from openai import OpenAI

from config import settings


logger = logging.getLogger(__name__)


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


def transcribe(audio_path: Path) -> Transcript:
    """Send the audio file to OpenAI Whisper and return word/segment timestamps."""

    if not audio_path.exists():
        raise FileNotFoundError(f"Audio file not found: {audio_path}")

    logger.info("Calling Whisper API for %s", audio_path.name)
    client = _client()

    with audio_path.open("rb") as fp:
        response: Any = client.audio.transcriptions.create(
            model=settings.WHISPER_MODEL,
            file=fp,
            response_format="verbose_json",
            timestamp_granularities=["word", "segment"],
        )

    raw = response.model_dump() if hasattr(response, "model_dump") else dict(response)

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

    transcript = Transcript(
        language=raw.get("language"),
        duration=raw.get("duration"),
        text=str(raw.get("text", "")),
        words=words,
        segments=segments,
    )
    logger.info(
        "Whisper returned %d words, %d segments, language=%s",
        len(transcript.words),
        len(transcript.segments),
        transcript.language,
    )
    return transcript
