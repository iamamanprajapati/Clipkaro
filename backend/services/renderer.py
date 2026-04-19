"""FFmpeg-driven clip renderer with ASS subtitle generation.

Generates a 1080x1920 vertical clip with two animated overlays:

1. A hook title centered near the top, visible for the first 2.5 seconds.
2. Word-by-word subtitles (4 words per line) at the bottom, with the
   currently-spoken word highlighted in yellow.

Devanagari rendering: defaults to "Noto Sans Devanagari" with "Arial" as
fallback. The README explains how to install the font system-wide.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List, Sequence

from services.face_layout import ClipLayout, Segment
from services.whisper import WordTimestamp


logger = logging.getLogger(__name__)


VIDEO_WIDTH = 1080
VIDEO_HEIGHT = 1920

PRIMARY_FONT = "Noto Sans Devanagari"
FALLBACK_FONT = "Arial"

WORDS_PER_LINE = 4
HOOK_DURATION_SEC = 2.5


_BUNDLED_BIN = Path(__file__).resolve().parent.parent / "bin"


def _resolve_binary(name: str) -> str | None:
    """Prefer a bundled binary in backend/bin, then fall back to PATH.

    Some Homebrew bottles of FFmpeg are built without libass / drawtext, which
    breaks subtitle rendering. We ship a known-good static build under
    backend/bin/ that includes libass — use it whenever it exists.
    """
    bundled = _BUNDLED_BIN / name
    if bundled.exists():
        return str(bundled)
    return shutil.which(name)


def ffmpeg_bin() -> str:
    path = _resolve_binary("ffmpeg")
    if path is None:
        raise RuntimeError(
            "ffmpeg not found. Either install it (e.g. `brew install ffmpeg-full`) "
            "or download a static build into backend/bin/ffmpeg."
        )
    return path


def ffprobe_bin() -> str | None:
    return _resolve_binary("ffprobe")


@dataclass
class RenderRequest:
    source_path: Path
    output_path: Path
    start_sec: float
    end_sec: float
    hook_text: str
    words: Sequence[WordTimestamp]
    ass_path: Path
    layout: ClipLayout | None = None


def ensure_ffmpeg() -> None:
    """Verify ffmpeg exists AND has the libass-based `subtitles` filter."""
    bin_path = ffmpeg_bin()
    completed = subprocess.run(
        [bin_path, "-hide_banner", "-filters"],
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"ffmpeg failed to start: {completed.stderr[-300:]}")
    if "subtitles" not in completed.stdout:
        raise RuntimeError(
            "Your ffmpeg build is missing libass (no `subtitles` filter). "
            "Install ffmpeg-full (`brew install ffmpeg-full`) or place a "
            "static libass-enabled build at backend/bin/ffmpeg."
        )


def extract_audio(video_path: Path, audio_path: Path) -> Path:
    """Extract mono 16kHz MP3 audio from a video for Whisper."""
    bin_path = ffmpeg_bin()
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        bin_path,
        "-y",
        "-i", str(video_path),
        "-vn",
        "-ac", "1",
        "-ar", "16000",
        "-b:a", "64k",
        str(audio_path),
    ]
    logger.info("Extracting audio: %s", " ".join(cmd))
    completed = subprocess.run(cmd, capture_output=True, text=True)
    if completed.returncode != 0:
        raise RuntimeError(
            f"ffmpeg audio extraction failed: {completed.stderr[-500:]}"
        )
    return audio_path


def probe_duration(video_path: Path) -> float | None:
    """Return the video duration in seconds via ffprobe, or None on failure."""
    bin_path = ffprobe_bin()
    if bin_path is None:
        return None
    cmd = [
        bin_path,
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(video_path),
    ]
    completed = subprocess.run(cmd, capture_output=True, text=True)
    if completed.returncode != 0:
        return None
    try:
        return float(completed.stdout.strip())
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# ASS subtitle generation
# ---------------------------------------------------------------------------


def _format_ass_time(seconds: float) -> str:
    """Format seconds as ASS time: H:MM:SS.cs (centiseconds)."""
    if seconds < 0:
        seconds = 0
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    return f"{hours}:{minutes:02d}:{secs:05.2f}"


def _escape_ass_text(text: str) -> str:
    """Escape characters that would break ASS dialogue lines."""
    return (
        text.replace("\\", "\\\\")
        .replace("{", "\\{")
        .replace("}", "\\}")
        .replace("\n", " ")
    )


def _ass_header(font_name: str = PRIMARY_FONT) -> str:
    """ASS script header with styles. PlayResY=1920 matches the output frame."""
    return f"""[Script Info]
ScriptType: v4.00+
PlayResX: {VIDEO_WIDTH}
PlayResY: {VIDEO_HEIGHT}
ScaledBorderAndShadow: yes
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Hook,{font_name},88,&H00FFFFFF,&H00FFFFFF,&H00000000,&HB0000000,1,0,0,0,100,100,0,0,3,8,0,8,80,80,40,1
Style: Sub,{font_name},72,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,1,0,0,0,100,100,0,0,1,5,2,2,80,80,80,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _hook_dialogue(hook_text: str, clip_duration: float) -> str:
    duration = min(HOOK_DURATION_SEC, max(0.5, clip_duration - 0.1))
    start = _format_ass_time(0.0)
    end = _format_ass_time(duration)
    pos_x = VIDEO_WIDTH // 2
    pos_y = int(VIDEO_HEIGHT * 0.25)
    safe = _escape_ass_text(hook_text)
    fade = "{\\fad(200,200)}"
    pos = f"{{\\an5\\pos({pos_x},{pos_y})}}"
    return f"Dialogue: 0,{start},{end},Hook,,0,0,0,,{pos}{fade}{safe}"


def _build_word_lines(
    words: Sequence[WordTimestamp],
    clip_start: float,
    clip_end: float,
) -> List[List[WordTimestamp]]:
    """Group words that fall inside the clip window into lines of N words."""
    in_window: List[WordTimestamp] = []
    for word in words:
        if word.end <= clip_start or word.start >= clip_end:
            continue
        rel_start = max(0.0, word.start - clip_start)
        rel_end = max(rel_start + 0.05, word.end - clip_start)
        in_window.append(WordTimestamp(word=word.word, start=rel_start, end=rel_end))

    lines: List[List[WordTimestamp]] = []
    for i in range(0, len(in_window), WORDS_PER_LINE):
        lines.append(in_window[i : i + WORDS_PER_LINE])
    return lines


def _subtitle_dialogues(
    words: Sequence[WordTimestamp],
    clip_start: float,
    clip_end: float,
) -> List[str]:
    """Render one ASS dialogue per word, highlighting the active word in yellow."""
    clip_duration = clip_end - clip_start
    lines = _build_word_lines(words, clip_start, clip_end)
    dialogues: List[str] = []

    for line_idx, line_words in enumerate(lines):
        if not line_words:
            continue
        line_start = line_words[0].start
        if line_idx + 1 < len(lines) and lines[line_idx + 1]:
            line_end = lines[line_idx + 1][0].start
        else:
            line_end = min(clip_duration, line_words[-1].end + 0.4)

        for word_idx, current in enumerate(line_words):
            seg_start = max(line_start, current.start) if word_idx == 0 else current.start
            seg_end = (
                line_words[word_idx + 1].start if word_idx + 1 < len(line_words) else line_end
            )
            if seg_end <= seg_start:
                seg_end = seg_start + 0.05

            parts: List[str] = []
            for j, w in enumerate(line_words):
                token = _escape_ass_text(w.word)
                if not token:
                    continue
                if j == word_idx:
                    parts.append("{\\c&H0000FFFF&}" + token + "{\\c&H00FFFFFF&}")
                else:
                    parts.append(token)
            text = " ".join(parts)
            if not text.strip():
                continue

            start_t = _format_ass_time(seg_start)
            end_t = _format_ass_time(seg_end)
            dialogues.append(
                f"Dialogue: 1,{start_t},{end_t},Sub,,0,0,0,,{{\\an2}}{text}"
            )

    return dialogues


def write_ass_file(
    ass_path: Path,
    hook_text: str,
    words: Sequence[WordTimestamp],
    clip_start: float,
    clip_end: float,
    font_name: str = PRIMARY_FONT,
) -> Path:
    ass_path.parent.mkdir(parents=True, exist_ok=True)
    clip_duration = max(0.5, clip_end - clip_start)
    body_lines = [_hook_dialogue(hook_text, clip_duration)]
    body_lines.extend(_subtitle_dialogues(words, clip_start, clip_end))
    content = _ass_header(font_name) + "\n".join(body_lines) + "\n"
    ass_path.write_text(content, encoding="utf-8")
    return ass_path


# ---------------------------------------------------------------------------
# FFmpeg invocation
# ---------------------------------------------------------------------------


def _escape_for_ffmpeg_filter(path: Path) -> str:
    """Escape an absolute path so it can be used inside an ffmpeg filtergraph.

    Inside `subtitles=...` we must escape characters that ffmpeg interprets
    in filter expressions: `:` `\\` `'` and the path quoting char.
    """
    text = str(path)
    text = text.replace("\\", "\\\\")
    text = text.replace(":", "\\:")
    text = text.replace("'", "\\'")
    return text


def _panel_crop_box(
    sh: int,
    x_start: int,
    x_end: int,
    face_x: int,
    face_y: int,
    panel_w: int,
    panel_h: int,
) -> tuple[int, int, int, int]:
    """Compute (crop_w, crop_h, crop_x, crop_y) for one stacked panel.

    The crop is bounded to the horizontal slice [x_start, x_end] of the source
    (so the *other* speaker can never leak in), shaped to the panel's aspect
    ratio, and centered on the detected face position.
    """
    avail_w = max(1, x_end - x_start)
    avail_h = sh
    panel_aspect = panel_w / panel_h
    avail_aspect = avail_w / avail_h

    if avail_aspect >= panel_aspect:
        crop_h = avail_h
        crop_w = max(1, int(round(crop_h * panel_aspect)))
    else:
        crop_w = avail_w
        crop_h = max(1, int(round(crop_w / panel_aspect)))

    cx_min = x_start + crop_w // 2
    cx_max = x_end - crop_w // 2
    if cx_min > cx_max:
        cx_min = cx_max = (x_start + x_end) // 2
    cx = max(cx_min, min(face_x, cx_max))
    crop_x = cx - crop_w // 2

    cy_min = crop_h // 2
    cy_max = sh - crop_h // 2
    if cy_min > cy_max:
        cy_min = cy_max = sh // 2
    cy = max(cy_min, min(face_y, cy_max))
    crop_y = cy - crop_h // 2

    return crop_w, crop_h, crop_x, crop_y


def _segment_crop_chain(seg: Segment, sw: int, sh: int, idx: int) -> str:
    """Filter chain that turns a trimmed source clip into a 1080x1920 frame.

    Returned string is everything *after* the trim+setpts of one segment and
    ends with a single output pixel stream sized exactly VIDEO_WIDTH x VIDEO_HEIGHT.
    Internal labels are suffixed with ``idx`` so multiple segments don't collide.
    """
    if seg.kind == "stacked" and sw > 0 and sh > 0:
        panel_w = VIDEO_WIDTH
        panel_h = VIDEO_HEIGHT // 2  # 960
        split_x = (seg.top_center_x + seg.bottom_center_x) // 2
        split_x = max(1, min(sw - 1, split_x))

        w1, h1, x1, y1 = _panel_crop_box(
            sh, 0, split_x, seg.top_center_x, seg.top_center_y, panel_w, panel_h
        )
        w2, h2, x2, y2 = _panel_crop_box(
            sh, split_x, sw, seg.bottom_center_x, seg.bottom_center_y, panel_w, panel_h
        )
        return (
            f"split=2[a{idx}][b{idx}];"
            f"[a{idx}]crop={w1}:{h1}:{x1}:{y1},scale={panel_w}:{panel_h}[top{idx}];"
            f"[b{idx}]crop={w2}:{h2}:{x2}:{y2},scale={panel_w}:{panel_h}[bot{idx}];"
            f"[top{idx}][bot{idx}]vstack=inputs=2"
        )

    # single — face-aware crop if we know where the speaker is
    if seg.single_center_x >= 0 and sw > 0 and sh > 0:
        crop_w = int(round(sh * VIDEO_WIDTH / VIDEO_HEIGHT))
        crop_w = min(crop_w, sw)
        cx_min = crop_w // 2
        cx_max = sw - crop_w // 2
        if cx_min > cx_max:
            cx = sw // 2
        else:
            cx = max(cx_min, min(seg.single_center_x, cx_max))
        crop_x = cx - crop_w // 2
        return f"crop={crop_w}:{sh}:{crop_x}:0,scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}"

    # No face info → fall back to the original geometric center crop.
    return f"crop=ih*9/16:ih,scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}"


def _build_filter_complex(
    layout: ClipLayout | None, duration: float, ass_arg: str
) -> str:
    """Compose a -filter_complex graph that switches layout per segment.

    The graph trims each segment from the source, applies the segment's crop
    chain, concatenates the segments in order, then burns subtitles on the
    full timeline (subtitle timestamps are clip-relative, which matches the
    concatenated output's PTS).
    """
    if layout is None or not layout.segments:
        return (
            f"[0:v]crop=ih*9/16:ih,scale={VIDEO_WIDTH}:{VIDEO_HEIGHT},"
            f"subtitles={ass_arg}[vout]"
        )

    sw, sh = layout.source_width, layout.source_height
    parts: List[str] = []
    seg_labels: List[str] = []

    for i, seg in enumerate(layout.segments):
        s = max(0.0, seg.start_offset)
        e = min(duration, max(s + 0.001, seg.end_offset))
        chain = _segment_crop_chain(seg, sw, sh, i)
        # Normalise pixel format AND aspect ratio across segments — concat
        # refuses inputs whose SAR or pix_fmt don't match, and the stacked vs
        # single chains naturally end up with slightly different SARs.
        parts.append(
            f"[0:v]trim={s:.3f}:{e:.3f},setpts=PTS-STARTPTS,{chain},"
            f"setsar=1,format=yuv420p[v{i}]"
        )
        seg_labels.append(f"[v{i}]")

    if len(seg_labels) == 1:
        parts.append(f"{seg_labels[0]}null[vcat]")
    else:
        parts.append(
            f"{''.join(seg_labels)}concat=n={len(seg_labels)}:v=1:a=0[vcat]"
        )
    parts.append(f"[vcat]subtitles={ass_arg}[vout]")
    return ";".join(parts)


def render_clip(req: RenderRequest) -> Path:
    """Render a single 9:16 clip with burned-in subtitles."""
    bin_path = ffmpeg_bin()

    write_ass_file(
        req.ass_path,
        req.hook_text,
        req.words,
        req.start_sec,
        req.end_sec,
    )

    duration = max(0.5, req.end_sec - req.start_sec)
    ass_arg = _escape_for_ffmpeg_filter(req.ass_path)
    filter_complex = _build_filter_complex(req.layout, duration, ass_arg)

    req.output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        bin_path,
        "-y",
        "-ss", f"{req.start_sec:.3f}",
        "-i", str(req.source_path),
        "-t", f"{duration:.3f}",
        "-filter_complex", filter_complex,
        "-map", "[vout]",
        "-map", "0:a?",
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "23",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "128k",
        "-movflags", "+faststart",
        str(req.output_path),
    ]
    logger.info(
        "Rendering clip -> %s (%d segment(s))",
        req.output_path.name,
        len(req.layout.segments) if req.layout else 0,
    )
    completed = subprocess.run(cmd, capture_output=True, text=True)
    if completed.returncode != 0:
        logger.error("ffmpeg cmd: %s", " ".join(cmd))
        logger.error("ffmpeg stderr (full): %s", completed.stderr)
        raise RuntimeError(f"ffmpeg render failed: {completed.stderr[-1500:]}")

    return req.output_path
