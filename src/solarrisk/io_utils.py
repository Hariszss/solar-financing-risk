"""HTTP retrieval with raw-response caching and a provenance manifest.

Every successful or failed request is logged in data/raw/manifest.jsonl with URL (secrets
redacted), HTTP status, retrieval time (UTC), size and SHA-256 of the body. This makes it
possible to prove later exactly which bytes the analysis was built on — important because
PVGIS 6 is explicitly "under active development" and may change its outputs.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import requests

log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass
class FetchResult:
    ok: bool
    status: int | None
    path: Path | None
    url: str
    error: str | None = None


def _redact(url: str) -> str:
    return re.sub(r"(securityToken=)[^&]+", r"\1<redacted>", url)


def fetch_to_file(
    url: str,
    out_path: Path,
    *,
    method: str = "GET",
    params: dict | None = None,
    data: dict | None = None,
    use_cache: bool = True,
    retries: int = 5,
    timeout: int = 120,
    manifest: Path | None = None,
) -> FetchResult:
    """Download `url` to `out_path`; reuse the cached file if present (use_cache=True)."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    manifest = manifest or PROJECT_ROOT / "data" / "raw" / "manifest.jsonl"

    if use_cache and out_path.exists() and out_path.stat().st_size > 0:
        log.info("cache hit: %s", out_path.name)
        return FetchResult(True, 200, out_path, url)

    last_err, status, full_url = None, None, url
    for attempt in range(1, retries + 1):
        try:
            r = requests.request(method, url, params=params, data=data, timeout=timeout)
            status, full_url = r.status_code, r.url
            if r.status_code == 200:
                out_path.write_bytes(r.content)
                _log_manifest(manifest, full_url, status, out_path, r.content, None)
                return FetchResult(True, status, out_path, _redact(full_url))
            last_err = f"HTTP {r.status_code}: {r.text[:300]}"
            if r.status_code == 429:  # rate limited: back off and retry
                wait = float(r.headers.get("Retry-After", 15 * attempt))
                log.info("HTTP 429, waiting %.0fs (attempt %d/%d)", wait, attempt, retries)
                time.sleep(min(wait, 120))
                continue
            if 400 <= r.status_code < 500:  # other client errors will not fix themselves
                break
        except requests.RequestException as e:  # network problems: retry
            last_err = repr(e)
        time.sleep(2 * attempt)

    _log_manifest(manifest, full_url, status, None, b"", last_err)
    log.warning("fetch failed: %s -> %s", _redact(full_url), last_err)
    return FetchResult(False, status, None, _redact(full_url), last_err)


def _log_manifest(manifest: Path, url, status, path, body: bytes, error):
    manifest.parent.mkdir(parents=True, exist_ok=True)
    rec = {
        "retrieved_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "url": _redact(url),
        "status": status,
        "file": str(path.relative_to(PROJECT_ROOT)) if path else None,
        "bytes": len(body),
        "sha256": hashlib.sha256(body).hexdigest() if body else None,
        "error": error,
    }
    with manifest.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")
