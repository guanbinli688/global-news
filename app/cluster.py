"""Group likely reports of one event without inflating evidence counts."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from .normalize import canonical_url, sortable_time, title_fingerprint


STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "has",
    "in", "is", "it", "of", "on", "that", "the", "to", "was", "with", "after",
    "new", "says", "over", "amid", "into", "its", "their", "this",
}


def title_tokens(title: str) -> set[str]:
    return {
        token for token in re.findall(r"[\w\-]+", title.casefold(), flags=re.UNICODE)
        if len(token) > 2 and token not in STOPWORDS
    }


def anchor_tokens(title: str) -> set[str]:
    words = re.findall(r"\b(?:[A-Z]{2,}|[A-Z][a-z]{2,}|\d{2,})\b", title)
    return {word.casefold() for word in words if word.casefold() not in STOPWORDS}


def similarity(left: str, right: str) -> float:
    a, b = title_tokens(left), title_tokens(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def hours_apart(left: dict[str, Any], right: dict[str, Any]) -> float:
    a, b = sortable_time(left), sortable_time(right)
    if a == datetime.min or b == datetime.min:
        return float("inf")
    return abs((a - b).total_seconds()) / 3600


def cluster_candidates(items: list[dict[str, Any]], threshold: float, window_hours: int) -> list[dict[str, Any]]:
    clusters: list[dict[str, Any]] = []
    for item in sorted(items, key=sortable_time, reverse=True):
        canonical = canonical_url(str(item.get("url") or ""))
        fingerprint = title_fingerprint(str(item.get("title") or ""))
        matched: dict[str, Any] | None = None
        for cluster in clusters:
            lead = cluster["items"][0]
            same_url = bool(canonical) and canonical == canonical_url(str(lead.get("url") or ""))
            same_title = bool(fingerprint) and fingerprint == title_fingerprint(str(lead.get("title") or ""))
            title_score = similarity(str(item.get("title") or ""), str(lead.get("title") or ""))
            shared_tokens = title_tokens(str(item.get("title") or "")) & title_tokens(str(lead.get("title") or ""))
            shared_anchors = anchor_tokens(str(item.get("title") or "")) & anchor_tokens(str(lead.get("title") or ""))
            related_wording = title_score >= threshold or (title_score >= .38 and len(shared_tokens) >= 3) or (title_score >= .28 and len(shared_anchors) >= 2)
            close_title = hours_apart(item, lead) <= window_hours and related_wording
            if same_url or same_title or close_title:
                matched = cluster
                break
        if matched is None:
            matched = {"items": [], "lead_title": item.get("title"), "latest_time": item.get("published_at")}
            clusters.append(matched)
        matched["items"].append(item)
    for cluster in clusters:
        cluster["independence_groups"] = sorted({str(item.get("independence_group") or item.get("source_id")) for item in cluster["items"]})
        cluster["upstream_origins"] = sorted({str(item.get("upstream_origin") or item.get("source_id")) for item in cluster["items"]})
    return clusters
