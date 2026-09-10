"""Normalize metadata without inventing article content."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


TRACKING_KEYS = {
    "at_campaign",
    "at_medium",
    "fbclid",
    "gclid",
    "ref",
    "source",
}


def canonical_url(raw: str) -> str:
    value = (raw or "").strip()
    if not value:
        return ""
    parts = urlsplit(value)
    query = [
        (key, item)
        for key, item in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() not in TRACKING_KEYS and not key.lower().startswith("utm_")
    ]
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, urlencode(query), ""))


def title_fingerprint(title: str) -> str:
    return re.sub(r"[^\w]+", "", (title or "").casefold(), flags=re.UNICODE)


def event_id(item: dict[str, Any]) -> str:
    stable = canonical_url(str(item.get("url", ""))) or title_fingerprint(str(item.get("title", "")))
    return "evt_" + hashlib.sha256(stable.encode("utf-8")).hexdigest()[:16]


def time_object(item: dict[str, Any]) -> dict[str, str | None]:
    value = item.get("published_at")
    precision = item.get("time_precision") or "unknown"
    return {
        "value": value,
        "precision": precision,
        "original_text": item.get("original_time"),
        "timezone": "UTC" if value and precision == "datetime" else None,
    }


def sortable_time(item: dict[str, Any]) -> datetime:
    value = item.get("published_at")
    if not value:
        return datetime.min
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        try:
            return datetime.fromisoformat(str(value)).replace(tzinfo=None)
        except ValueError:
            return datetime.min
