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


# Hard business rules for clip duration. These are enforced in code AFTER
# Claude returns, so even if the model misbehaves we never emit a sub-30s clip.
MIN_CLIP_SEC = 30.0
MAX_CLIP_SEC = 60.0
TARGET_CLIP_SEC = 45.0


SYSTEM_PROMPT = """You are an expert short-form video editor for Indian creators.
You receive a timestamped transcript of a long Hindi/Hinglish/English video
(podcast, interview, monologue) and must select the 5 BEST self-contained
moments to turn into vertical short clips for Instagram Reels and YouTube Shorts.

Selection rules:
- Each clip MUST be at least 30 seconds and at most 60 seconds long.
  Aim for ~45 seconds. NEVER return a clip shorter than 30 seconds — a
  short clip has zero retention and is worse than no clip at all.
- Each clip must be SELF-CONTAINED: a viewer who never saw the rest must
  understand and feel the moment. Pick natural start and end points (a full
  thought, not mid-sentence).
- If a punchy moment is only 10-15 seconds on its own, EXPAND the window
  outward to include the setup before it and/or the reaction after it so
  the final clip still lands in the 30-60s range.
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


def _enforce_min_duration(
    pick: ClipPick,
    transcript: Transcript,
) -> ClipPick | None:
    """Extend a too-short pick to at least MIN_CLIP_SEC using transcript segments.

    We grow the window outward by snapping to whole transcript segments so
    the extended clip still starts/ends on a natural sentence boundary.
    If the source video itself is shorter than MIN_CLIP_SEC we return the
    pick clamped to the video bounds (best we can do). If for any reason
    we still can't reach the minimum, we return None so the caller drops
    the pick instead of rendering a useless 2-second clip.
    """
    video_end = transcript.duration or pick.end_sec
    start = max(0.0, min(pick.start_sec, video_end))
    end = max(start, min(pick.end_sec, video_end))

    # If the whole video is shorter than our minimum, there's nothing to
    # extend. Return the widest possible window.
    if video_end <= MIN_CLIP_SEC:
        return ClipPick(
            start_sec=0.0,
            end_sec=video_end,
            hook=pick.hook,
            reason_picked=pick.reason_picked,
        )

    if end - start >= MIN_CLIP_SEC:
        # Already long enough — still cap the upper bound.
        if end - start > MAX_CLIP_SEC:
            end = start + MAX_CLIP_SEC
        return ClipPick(start, end, pick.hook, pick.reason_picked)

    target = min(TARGET_CLIP_SEC, video_end)
    segments = transcript.segments or []

    # Sorted segment start/end points we can snap to.
    seg_starts = sorted({max(0.0, s.start) for s in segments if s.end > s.start})
    seg_ends = sorted({min(video_end, s.end) for s in segments if s.end > s.start})

    def _next_end_after(t: float) -> float | None:
        for e in seg_ends:
            if e > t + 0.05:
                return e
        return None

    def _prev_start_before(t: float) -> float | None:
        candidate: float | None = None
        for s in seg_starts:
            if s < t - 0.05:
                candidate = s
            else:
                break
        return candidate

    # 1) Try to grow the END forward to the next segment boundary(ies).
    while end - start < target:
        nxt = _next_end_after(end)
        if nxt is None or nxt <= end:
            break
        end = min(video_end, nxt)
        if end - start >= target:
            break

    # 2) If still short, grow the START backward to a previous segment start.
    while end - start < target:
        prv = _prev_start_before(start)
        if prv is None or prv >= start:
            break
        start = max(0.0, prv)
        if end - start >= target:
            break

    # 3) Final fallback: if transcript snapping didn't get us there (e.g. very
    #    sparse segments), just pad with raw seconds clamped to the video.
    if end - start < MIN_CLIP_SEC:
        needed = MIN_CLIP_SEC - (end - start)
        grow_end = min(video_end - end, needed)
        end += grow_end
        needed -= grow_end
        if needed > 0:
            grow_start = min(start, needed)
            start -= grow_start

    if end - start < MIN_CLIP_SEC:
        # Shouldn't happen given video_end > MIN_CLIP_SEC, but be safe.
        return None

    if end - start > MAX_CLIP_SEC:
        # If we overshot, shrink from the end.
        end = start + MAX_CLIP_SEC

    return ClipPick(start, end, pick.hook, pick.reason_picked)


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

        raw_duration = pick.end_sec - pick.start_sec
        adjusted = _enforce_min_duration(pick, transcript)
        if adjusted is None:
            logger.warning(
                "Dropping clip '%s' — could not extend %.1fs clip to %ds minimum",
                pick.hook,
                raw_duration,
                int(MIN_CLIP_SEC),
            )
            continue

        final_duration = adjusted.end_sec - adjusted.start_sec
        if abs(final_duration - raw_duration) > 0.5:
            logger.info(
                "Extended clip '%s' from %.1fs to %.1fs (snapped to segment boundaries)",
                adjusted.hook,
                raw_duration,
                final_duration,
            )

        picks.append(adjusted)

    if not picks:
        raise ValueError("No usable clip picks returned by Claude")

    logger.info("Claude picked %d clips", len(picks))
    return picks[:5]
