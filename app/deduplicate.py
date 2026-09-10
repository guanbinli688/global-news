"""Conservative metadata deduplication for the preview."""

from __future__ import annotations

from typing import Any

from .normalize import canonical_url, sortable_time, title_fingerprint


def deduplicate(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse exact URL/title matches while retaining the freshest record.

    Matching titles from republishers are one candidate event, not independent
    verification. The complete set of source records remains attached internally.
    """
    result: list[dict[str, Any]] = []
    url_index: dict[str, int] = {}
    title_index: dict[str, int] = {}
    for original in sorted(items, key=sortable_time, reverse=True):
        item = dict(original)
        url_key = canonical_url(str(item.get("url", "")))
        title_key = title_fingerprint(str(item.get("title", "")))
        match = url_index.get(url_key) if url_key else None
        if match is None and title_key:
            match = title_index.get(title_key)
        if match is not None:
            merged = result[match].setdefault("duplicate_sources", [])
            merged.append({
                "source_id": item.get("source_id"),
                "source": item.get("source"),
                "url": item.get("url"),
                "upstream_origin": item.get("upstream_origin"),
                "independence_group": item.get("independence_group"),
            })
            continue
        index = len(result)
        item["canonical_url"] = url_key
        item["duplicate_sources"] = []
        result.append(item)
        if url_key:
            url_index[url_key] = index
        if title_key:
            title_index[title_key] = index
    return result
