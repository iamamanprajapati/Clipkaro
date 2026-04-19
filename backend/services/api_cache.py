"""On-disk cache for paid API calls (Whisper transcripts, Claude clip picks).

The goal is simple: when the developer re-tests with the same input video,
we should NOT re-charge the OpenAI / Anthropic accounts. We key everything
by SHA-256 of stable inputs and store the response as JSON next to the
storage directory.

Disable globally by setting `ENABLE_API_CACHE=false` in `backend/.env`.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Optional

from config import settings


logger = logging.getLogger(__name__)


_HASH_CHUNK = 1024 * 1024  # 1 MiB


def cache_enabled() -> bool:
    return bool(settings.ENABLE_API_CACHE)


def hash_file(path: Path) -> str:
    """Stream a SHA-256 of the file contents."""
    sha = hashlib.sha256()
    with path.open("rb") as fp:
        while True:
            chunk = fp.read(_HASH_CHUNK)
            if not chunk:
                break
            sha.update(chunk)
    return sha.hexdigest()


def hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_json(cache_path: Path) -> Optional[Any]:
    """Return the parsed JSON payload, or None if the cache file is missing
    / unreadable / corrupt. We never raise from the cache layer."""
    if not cache_enabled():
        return None
    if not cache_path.exists():
        return None
    try:
        with cache_path.open("r", encoding="utf-8") as fp:
            return json.load(fp)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Ignoring corrupt cache file %s: %s", cache_path, exc)
        return None


def save_json(cache_path: Path, payload: Any) -> None:
    """Write JSON to disk atomically. Failures are logged but not fatal."""
    if not cache_enabled():
        return
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache_path.with_suffix(cache_path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as fp:
            json.dump(payload, fp, ensure_ascii=False, indent=2)
        tmp.replace(cache_path)
    except OSError as exc:
        logger.warning("Failed to write cache file %s: %s", cache_path, exc)
