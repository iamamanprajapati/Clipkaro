"""Use Claude Haiku to pick the 5 best clip moments and write hook titles."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import List

from anthropic import Anthropic

from config import settings
from services.whisper import Transcript


logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """You are an expert short-form video editor for Indian creators.
You receive a timestamped transcript of a long Hindi/Hinglish/English video
(podcast, interview, monologue) and must select the 5 BEST self-contained
moments to turn into vertical short clips for Instagram Reels and YouTube Shorts.

Selection rules:
- Each clip must be between 30 and 60 seconds long.
- Each clip must be SELF-CONTAINED: a viewer who never saw the rest must
  understand and feel the moment. Pick natural start and end points (a full
  thought, not mid-sentence).
- Prioritise moments with: strong opinions, surprising facts, emotional
  stories, aha insights, actionable advice, or genuinely funny lines.
- Spread the picks across the video; do not pick 5 overlapping moments.
- Clips must NOT overlap each other.

Hook title rules:
- Write a 5-8 word hook for each clip.
- Match the language of the source video. If the video is Hinglish, write
  the hook in Hinglish (Latin script). If pure Hindi, you may use Devanagari.
  If English, write in English.
- No clickbait, no ALL CAPS, no excessive punctuation.
- The hook should make a scrolling viewer stop and watch.

Output rules:
- Return ONLY valid JSON in this exact shape, with no markdown fences and no
  prose around it:
  {
    "clips": [
      {"start_sec": <float>, "end_sec": <float>, "hook": "<string>", "reason_picked": "<string>"}
    ]
  }
- Exactly 5 entries in the clips array.
- start_sec and end_sec are in seconds (floats), within the transcript bounds.
"""


@dataclass
class ClipPick:
    start_sec: float
    end_sec: float
    hook: str
    reason_picked: str


def _client() -> Anthropic:
    if not settings.ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY is not configured. Add it to backend/.env")
    return Anthropic(api_key=settings.ANTHROPIC_API_KEY)


def build_timestamped_transcript(transcript: Transcript) -> str:
    """Render segments as `[start - end] text` lines for the LLM prompt."""
    lines: List[str] = []
    for seg in transcript.segments:
        text = seg.text.strip()
        if not text:
            continue
        lines.append(f"[{seg.start:.1f}s - {seg.end:.1f}s] {text}")
    return "\n".join(lines)


def _strip_json_fences(raw: str) -> str:
    """Claude sometimes wraps JSON in ```json ... ``` fences. Strip them."""
    text = raw.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, flags=re.DOTALL)
    if fence:
        return fence.group(1).strip()
    return text


def _coerce_clip(entry: dict) -> ClipPick:
    return ClipPick(
        start_sec=float(entry["start_sec"]),
        end_sec=float(entry["end_sec"]),
        hook=str(entry.get("hook", "")).strip() or "Untitled clip",
        reason_picked=str(entry.get("reason_picked", "")).strip(),
    )


def pick_clips(transcript: Transcript) -> List[ClipPick]:
    """Ask Claude Haiku for 5 clip picks. Returns up to 5 validated picks."""

    if not transcript.segments:
        raise ValueError("Cannot pick clips from an empty transcript")

    timestamped = build_timestamped_transcript(transcript)
    duration_hint = (
        f"\nVideo duration: {transcript.duration:.1f} seconds." if transcript.duration else ""
    )
    language_hint = (
        f"\nDetected language: {transcript.language}." if transcript.language else ""
    )

    user_message = (
        f"Here is the timestamped transcript.{language_hint}{duration_hint}\n\n"
        f"{timestamped}\n\n"
        "Pick 5 clips per the rules and return strict JSON."
    )

    logger.info("Calling Claude (%s) to pick clips", settings.CLAUDE_MODEL)
    client = _client()
    response = client.messages.create(
        model=settings.CLAUDE_MODEL,
        max_tokens=2000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )

    raw_text = "".join(
        block.text for block in response.content if getattr(block, "type", None) == "text"
    )
    logger.debug("Claude raw response: %s", raw_text)
    cleaned = _strip_json_fences(raw_text)

    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if not match:
            raise ValueError(f"Claude returned non-JSON output: {raw_text[:200]}") from exc
        payload = json.loads(match.group(0))

    raw_clips = payload.get("clips", [])
    if not isinstance(raw_clips, list) or not raw_clips:
        raise ValueError("Claude response missing 'clips' array")

    picks: List[ClipPick] = []
    for entry in raw_clips:
        try:
            pick = _coerce_clip(entry)
        except (KeyError, TypeError, ValueError):
            logger.warning("Skipping malformed clip entry: %s", entry)
            continue

        if pick.end_sec <= pick.start_sec:
            continue

        if transcript.duration:
            pick.start_sec = max(0.0, min(pick.start_sec, transcript.duration))
            pick.end_sec = max(0.0, min(pick.end_sec, transcript.duration))

        duration = pick.end_sec - pick.start_sec
        if duration < 15 or duration > 90:
            logger.warning(
                "Clip duration %.1fs outside sanity range, keeping anyway", duration
            )

        picks.append(pick)

    if not picks:
        raise ValueError("No usable clip picks returned by Claude")

    logger.info("Claude picked %d clips", len(picks))
    return picks[:5]
