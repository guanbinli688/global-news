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

COUNTRY_REGION_ALIASES = {
    "argentina": "latin_america_caribbean", "阿根廷": "latin_america_caribbean",
    "australia": "oceania_pacific", "澳大利亚": "oceania_pacific",
    "brazil": "latin_america_caribbean", "巴西": "latin_america_caribbean",
    "canada": "north_america", "加拿大": "north_america",
    "china": "east_asia", "中国": "east_asia",
    "djibouti": "sub_saharan_africa", "吉布提": "sub_saharan_africa",
    "ethiopia": "sub_saharan_africa", "埃塞俄比亚": "sub_saharan_africa",
    "france": "europe_russia", "法国": "europe_russia",
    "germany": "europe_russia", "德国": "europe_russia",
    "india": "south_asia", "印度": "south_asia",
    "indonesia": "southeast_asia", "印度尼西亚": "southeast_asia", "印尼": "southeast_asia",
    "iran": "mena", "伊朗": "mena",
    "israel": "mena", "以色列": "mena",
    "japan": "east_asia", "日本": "east_asia",
    "kazakhstan": "central_asia_caucasus", "哈萨克斯坦": "central_asia_caucasus",
    "kenya": "sub_saharan_africa", "肯尼亚": "sub_saharan_africa",
    "malaysia": "southeast_asia", "马来西亚": "southeast_asia",
    "mexico": "latin_america_caribbean", "墨西哥": "latin_america_caribbean",
    "new zealand": "oceania_pacific", "新西兰": "oceania_pacific",
    "nigeria": "sub_saharan_africa", "尼日利亚": "sub_saharan_africa",
    "pakistan": "south_asia", "巴基斯坦": "south_asia",
    "philippines": "southeast_asia", "菲律宾": "southeast_asia",
    "russia": "europe_russia", "俄罗斯": "europe_russia",
    "saudi arabia": "mena", "沙特阿拉伯": "mena", "沙特": "mena",
    "singapore": "southeast_asia", "新加坡": "southeast_asia",
    "south africa": "sub_saharan_africa", "南非": "sub_saharan_africa",
    "south korea": "east_asia", "韩国": "east_asia",
    "timor-leste": "southeast_asia", "east timor": "southeast_asia", "东帝汶": "southeast_asia",
    "ukraine": "europe_russia", "乌克兰": "europe_russia",
    "united kingdom": "europe_russia", "英国": "europe_russia",
    "united states": "north_america", "usa": "north_america", "美国": "north_america",
    "vanuatu": "oceania_pacific", "瓦努阿图": "oceania_pacific",
    "vietnam": "southeast_asia", "越南": "southeast_asia",
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


def normalize_analysis_geography(analysis: dict[str, Any]) -> dict[str, Any]:
    """Replace model regions only when every named country has a curated mapping."""
    countries = [str(value).strip() for value in analysis.get("countries", []) if str(value).strip()]
    mapped = [COUNTRY_REGION_ALIASES.get(country.casefold()) for country in countries]
    if countries and all(mapped):
        normalized = dict(analysis)
        normalized["region_ids"] = sorted(set(mapped))
        return normalized
    return analysis


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
