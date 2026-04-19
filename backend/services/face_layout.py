"""Per-segment face layout detection.

For each clip we sample faces densely across the timeline and split it into
time segments where each segment has its own layout decision:

* ``single`` — one speaker on screen → renderer does a face-centered 9:16
  crop (no separator, full-frame).
* ``stacked`` — two speakers on screen → renderer crops each speaker out of
  one half of the source and stacks them top/bottom.

The result is a list of ``Segment`` objects covering the whole clip from
``start_offset`` 0 to ``end_offset == duration``. The renderer trims each
segment from the source and concatenates them, so the layout can flip back
and forth within a single clip.

If OpenCV isn't available, no faces are found, or anything else goes wrong,
we return a single full-clip ``single`` segment with no face focus, which
makes the renderer fall back to the original 9:16 center crop.
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)


# A 9:16 output split into two panels = 1080x960 each ⇒ panel aspect 9:8.
PANEL_ASPECT_W = 9
PANEL_ASPECT_H = 8

# How often (in seconds of clip time) we run face detection. Tighter sampling
# = better boundary precision for layout switches, but more CPU.
_SAMPLE_INTERVAL_SEC = 1.5

# Cap the total number of detection samples per clip so very long clips don't
# blow up. 25 samples × ~150ms ≈ ~4s of detection per clip.
_MAX_SAMPLES = 25
_MIN_SAMPLES = 4

# Smallest segment we'll ever produce. Anything shorter is merged into its
# longer neighbour so the layout doesn't flicker on every spurious detection.
_MIN_SEGMENT_SEC = 2.0

# Minimum face side, as a fraction of frame height. Filters out tiny / spurious
# detections (a hand, a mug, a microphone) so we only keep real speakers.
_MIN_FACE_FRAC = 0.10


@dataclass
class Segment:
    start_offset: float  # seconds, relative to clip start (0 = clip start)
    end_offset: float
    kind: str  # "single" or "stacked"
    # For single: x-center (in source pixels) of the speaker's face. -1 means
    # "no face detected, fall back to a center crop".
    single_center_x: int = -1
    # For stacked: pixel-space center of each face in the source frame.
    top_center_x: int = 0
    top_center_y: int = 0
    bottom_center_x: int = 0
    bottom_center_y: int = 0


@dataclass
class ClipLayout:
    source_width: int
    source_height: int
    duration: float
    segments: List[Segment] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Sampling helpers
# ---------------------------------------------------------------------------


@dataclass
class _Sample:
    time_offset: float  # seconds since clip start
    kind: str  # "single", "stacked", or "none" (no useful detection)
    faces: List[Tuple[float, float]]  # one or two (cx, cy) tuples


def _plan_sample_offsets(duration: float) -> List[float]:
    """Pick centers of equal-width slices across the clip duration."""
    if duration <= 0:
        return [0.0]
    n = max(_MIN_SAMPLES, min(_MAX_SAMPLES, int(round(duration / _SAMPLE_INTERVAL_SEC))))
    n = max(n, 1)
    slice_w = duration / n
    return [slice_w * (i + 0.5) for i in range(n)]


def _detect_faces_in_frame(
    cv2_module,
    frame_gray,
    cascades,
    min_side: int,
    width: int,
) -> List[Tuple[int, int, int, int]]:
    """Run every cascade (frontal + profile, original + flipped) and dedupe."""
    raw: List[Tuple[int, int, int, int]] = []
    for cascade, flip in cascades:
        if cascade.empty():
            continue
        img = cv2_module.flip(frame_gray, 1) if flip else frame_gray
        try:
            detected = cascade.detectMultiScale(
                img,
                scaleFactor=1.15,
                minNeighbors=4,
                minSize=(min_side, min_side),
            )
        except cv2_module.error:
            continue
        for x, y, w, h in detected:
            if flip:
                x = width - int(x) - int(w)
            raw.append((int(x), int(y), int(w), int(h)))

    raw.sort(key=lambda r: r[2] * r[3], reverse=True)
    kept: List[Tuple[int, int, int, int]] = []
    min_dist = max(40, int(width * 0.08))
    for face in raw:
        cx = face[0] + face[2] / 2
        cy = face[1] + face[3] / 2
        if all(
            ((cx - (kf[0] + kf[2] / 2)) ** 2 + (cy - (kf[1] + kf[3] / 2)) ** 2) ** 0.5
            >= min_dist
            for kf in kept
        ):
            kept.append(face)
        if len(kept) >= 2:
            break
    return kept


def _classify_sample(
    faces: List[Tuple[int, int, int, int]],
    width: int,
    can_stack: bool,
    time_offset: float,
) -> _Sample:
    """Decide whether this single sampled frame is single / stacked / none."""
    if not faces:
        return _Sample(time_offset=time_offset, kind="none", faces=[])

    if not can_stack or len(faces) == 1:
        f = max(faces, key=lambda r: r[2] * r[3])
        return _Sample(
            time_offset=time_offset,
            kind="single",
            faces=[(f[0] + f[2] / 2, f[1] + f[3] / 2)],
        )

    largest = sorted(faces, key=lambda r: r[2] * r[3], reverse=True)[:2]
    centers = [(f[0] + f[2] / 2, f[1] + f[3] / 2) for f in largest]
    centers.sort(key=lambda p: p[0])
    midline = width / 2
    if centers[0][0] < midline <= centers[1][0]:
        return _Sample(time_offset=time_offset, kind="stacked", faces=centers)

    f = max(faces, key=lambda r: r[2] * r[3])
    return _Sample(
        time_offset=time_offset,
        kind="single",
        faces=[(f[0] + f[2] / 2, f[1] + f[3] / 2)],
    )


# ---------------------------------------------------------------------------
# Smoothing & segmentation
# ---------------------------------------------------------------------------


def _fill_unknown_kinds(samples: List[_Sample]) -> List[str]:
    """Replace 'none' entries with the nearest known kind so smoothing works."""
    kinds = [s.kind for s in samples]

    last_known: Optional[str] = None
    for i, k in enumerate(kinds):
        if k != "none":
            last_known = k
        elif last_known is not None:
            kinds[i] = last_known

    next_known: Optional[str] = None
    for i in range(len(kinds) - 1, -1, -1):
        if kinds[i] != "none":
            next_known = kinds[i]
        elif next_known is not None:
            kinds[i] = next_known

    return ["single" if k == "none" else k for k in kinds]


def _majority_smooth(kinds: List[str], window: int = 3) -> List[str]:
    """3-sample majority filter, kills single-sample flickers."""
    if len(kinds) < window:
        return list(kinds)
    half = window // 2
    out = list(kinds)
    for i in range(len(kinds)):
        lo = max(0, i - half)
        hi = min(len(kinds), i + half + 1)
        most_common, _ = Counter(kinds[lo:hi]).most_common(1)[0]
        out[i] = most_common
    return out


def _build_segments(
    samples: List[_Sample],
    smoothed_kinds: List[str],
    duration: float,
    source_width: int,
    source_height: int,
) -> List[Segment]:
    """Group consecutive same-kind samples into time segments and merge shorts."""
    if not samples:
        return []

    n = len(samples)
    # Slice each sample covers [a, b]: midpoints between consecutive samples.
    boundaries: List[float] = [0.0]
    for i in range(n - 1):
        boundaries.append((samples[i].time_offset + samples[i + 1].time_offset) / 2)
    boundaries.append(duration)

    # Initial runs of identical kinds.
    runs: List[Tuple[str, float, float, List[int]]] = []  # kind, start, end, sample_idxs
    i = 0
    while i < n:
        j = i + 1
        while j < n and smoothed_kinds[j] == smoothed_kinds[i]:
            j += 1
        runs.append((smoothed_kinds[i], boundaries[i], boundaries[j], list(range(i, j))))
        i = j

    # Merge runs shorter than _MIN_SEGMENT_SEC into a neighbour.
    changed = True
    while changed and len(runs) > 1:
        changed = False
        for k, (kind, s, e, idxs) in enumerate(runs):
            if e - s >= _MIN_SEGMENT_SEC:
                continue
            # Pick the longer neighbour to absorb this short run.
            left = runs[k - 1] if k > 0 else None
            right = runs[k + 1] if k + 1 < len(runs) else None
            if left and right:
                target = left if (left[2] - left[1]) >= (right[2] - right[1]) else right
            else:
                target = left or right
            if target is left:
                runs[k - 1] = (left[0], left[1], e, left[3] + idxs)
                runs.pop(k)
            else:
                runs[k + 1] = (right[0], s, right[2], idxs + right[3])
                runs.pop(k)
            changed = True
            break

    # Re-collapse adjacent runs of the same kind that just got merged.
    coalesced: List[Tuple[str, float, float, List[int]]] = []
    for run in runs:
        if coalesced and coalesced[-1][0] == run[0]:
            prev = coalesced[-1]
            coalesced[-1] = (prev[0], prev[1], run[2], prev[3] + run[3])
        else:
            coalesced.append(run)

    # Build final Segment objects with averaged face positions.
    segments: List[Segment] = []
    for kind, s, e, idxs in coalesced:
        seg = Segment(start_offset=s, end_offset=e, kind=kind)
        if kind == "stacked":
            lefts_x: List[float] = []
            lefts_y: List[float] = []
            rights_x: List[float] = []
            rights_y: List[float] = []
            for idx in idxs:
                sample = samples[idx]
                if sample.kind == "stacked" and len(sample.faces) >= 2:
                    a, b = sample.faces[0], sample.faces[1]
                    if a[0] > b[0]:
                        a, b = b, a
                    lefts_x.append(a[0]); lefts_y.append(a[1])
                    rights_x.append(b[0]); rights_y.append(b[1])
                elif sample.kind == "single" and sample.faces:
                    cx, cy = sample.faces[0]
                    if cx < source_width / 2:
                        lefts_x.append(cx); lefts_y.append(cy)
                    else:
                        rights_x.append(cx); rights_y.append(cy)
            if lefts_x and rights_x:
                seg.top_center_x = int(sum(lefts_x) / len(lefts_x))
                seg.top_center_y = int(sum(lefts_y) / len(lefts_y))
                seg.bottom_center_x = int(sum(rights_x) / len(rights_x))
                seg.bottom_center_y = int(sum(rights_y) / len(rights_y))
            else:
                # Lost confidence in the second person — degrade to single.
                seg.kind = "single"
                if lefts_x:
                    seg.single_center_x = int(sum(lefts_x) / len(lefts_x))
                elif rights_x:
                    seg.single_center_x = int(sum(rights_x) / len(rights_x))
        else:  # single
            xs: List[float] = []
            for idx in idxs:
                sample = samples[idx]
                for cx, _cy in sample.faces:
                    xs.append(cx)
            if xs:
                seg.single_center_x = int(sum(xs) / len(xs))

        segments.append(seg)

    # Final coalescing in case a degraded 'stacked'→'single' now matches a neighbour.
    final: List[Segment] = []
    for seg in segments:
        if (
            final
            and final[-1].kind == seg.kind == "single"
            and final[-1].single_center_x == seg.single_center_x
        ):
            final[-1].end_offset = seg.end_offset
        elif final and final[-1].kind == seg.kind == "single":
            # Average the focus across the merge.
            prev = final[-1]
            prev_dur = prev.end_offset - prev.start_offset
            this_dur = seg.end_offset - seg.start_offset
            if prev.single_center_x >= 0 and seg.single_center_x >= 0:
                prev.single_center_x = int(
                    (prev.single_center_x * prev_dur + seg.single_center_x * this_dur)
                    / (prev_dur + this_dur)
                )
            elif seg.single_center_x >= 0:
                prev.single_center_x = seg.single_center_x
            prev.end_offset = seg.end_offset
        else:
            final.append(seg)

    return final


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def detect_layout(source_path: Path, start_sec: float, end_sec: float) -> ClipLayout:
    """Inspect the clip range and return a per-segment layout plan."""

    duration = max(0.0, end_sec - start_sec)
    fallback = ClipLayout(
        source_width=0,
        source_height=0,
        duration=duration,
        segments=[Segment(start_offset=0.0, end_offset=duration, kind="single")],
    )

    try:
        import cv2  # type: ignore
    except ImportError:
        logger.warning("OpenCV not available; using single-person layout")
        return fallback

    cap = cv2.VideoCapture(str(source_path))
    if not cap.isOpened():
        logger.warning("Could not open %s for face detection", source_path)
        return fallback

    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    if width <= 0 or height <= 0 or duration <= 0:
        cap.release()
        return fallback

    haar_root = cv2.data.haarcascades
    cascades = [
        (cv2.CascadeClassifier(haar_root + "haarcascade_frontalface_default.xml"), False),
        (cv2.CascadeClassifier(haar_root + "haarcascade_frontalface_alt2.xml"), False),
        (cv2.CascadeClassifier(haar_root + "haarcascade_profileface.xml"), False),
        (cv2.CascadeClassifier(haar_root + "haarcascade_profileface.xml"), True),
    ]
    if all(c.empty() for c, _ in cascades):
        logger.warning("No Haar cascades found; using single layout")
        cap.release()
        return ClipLayout(
            source_width=width,
            source_height=height,
            duration=duration,
            segments=[Segment(start_offset=0.0, end_offset=duration, kind="single")],
        )

    panel_crop_w = int(round(height * PANEL_ASPECT_W / PANEL_ASPECT_H))
    can_stack = panel_crop_w < width

    min_side = max(60, int(height * _MIN_FACE_FRAC))

    samples: List[_Sample] = []
    for offset in _plan_sample_offsets(duration):
        absolute = start_sec + offset
        cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, absolute) * 1000.0)
        ok, frame = cap.read()
        if not ok or frame is None:
            samples.append(_Sample(time_offset=offset, kind="none", faces=[]))
            continue
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.equalizeHist(gray)
        kept = _detect_faces_in_frame(cv2, gray, cascades, min_side, width)
        samples.append(_classify_sample(kept, width, can_stack, offset))

    cap.release()

    if not samples or all(s.kind == "none" for s in samples):
        return ClipLayout(
            source_width=width,
            source_height=height,
            duration=duration,
            segments=[Segment(start_offset=0.0, end_offset=duration, kind="single")],
        )

    raw_kinds = _fill_unknown_kinds(samples)
    smoothed = _majority_smooth(raw_kinds, window=3)
    segments = _build_segments(samples, smoothed, duration, width, height)

    if not segments:
        segments = [Segment(start_offset=0.0, end_offset=duration, kind="single")]

    return ClipLayout(
        source_width=width,
        source_height=height,
        duration=duration,
        segments=segments,
    )
