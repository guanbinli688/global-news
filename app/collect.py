"""Policy-aware collection for local previews and cloud runs.

The collector never sends cookies, never crosses a configured content boundary,
and treats remote text as untrusted data rather than executable instructions.
"""

from __future__ import annotations

import html
import ipaddress
import json
import re
import socket
import time
import urllib.error
import urllib.request
import urllib.robotparser
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo

from .common import STATE_DIR, load_json
from .source_diagnostics import feed_link, iso_utc, parse_time


def load_latest_items() -> tuple[str, list[dict[str, Any]]]:
    payload = load_json(STATE_DIR / "latest-items.json")
    if payload.get("content_policy") != "metadata_only_no_generated_analysis":
        raise ValueError("latest-items.json violates the metadata-only preview policy")
    items = payload.get("items", [])
    if not isinstance(items, list):
        raise ValueError("latest-items.json items must be an array")
    return str(payload.get("as_of") or ""), items


class TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.ignored = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() in {"script", "style", "noscript", "svg", "template"}:
            self.ignored += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() in {"script", "style", "noscript", "svg", "template"} and self.ignored:
            self.ignored -= 1

    def handle_data(self, data: str) -> None:
        if not self.ignored:
            self.parts.append(data)


def plain_text(raw: str, limit: int) -> str:
    parser = TextExtractor()
    parser.feed(raw or "")
    value = html.unescape(" ".join(parser.parts))
    value = re.sub(r"\s+", " ", value).strip()
    return value[:limit]


def _child_text(node: ET.Element, names: set[str]) -> str:
    for child in node.iter():
        if child.tag.rsplit("}", 1)[-1].casefold() in names and child.text:
            return child.text.strip()
    return ""


def parse_rss_candidates(payload: bytes, max_items: int, excerpt_chars: int) -> list[dict[str, Any]]:
    root = ET.fromstring(payload)
    root_name = root.tag.rsplit("}", 1)[-1].casefold()
    if root_name not in {"rss", "feed", "rdf"}:
        raise ValueError(f"unexpected XML root element: {root_name}")
    rows = [node for node in root.iter() if node.tag.rsplit("}", 1)[-1].casefold() in {"item", "entry"}]
    result: list[dict[str, Any]] = []
    for node in rows[:max_items]:
        raw_time = _child_text(node, {"pubdate", "published", "updated", "date", "created"})
        published_at, precision = parse_time(raw_time)
        raw_excerpt = _child_text(node, {"description", "summary", "content", "encoded"})
        result.append({
            "title": _child_text(node, {"title"}) or "（无标题）",
            "url": feed_link(node),
            "published_at": published_at,
            "time_precision": precision,
            "original_time": raw_time or None,
            "feed_excerpt": plain_text(raw_excerpt, excerpt_chars),
        })
    return result


def parse_usgs_candidates(payload: bytes, max_items: int) -> list[dict[str, Any]]:
    data = json.loads(payload.decode("utf-8-sig"))
    result: list[dict[str, Any]] = []
    for row in data.get("features", [])[:max_items]:
        props = row.get("properties", {})
        raw_ms = props.get("time")
        published_at = iso_utc(datetime.fromtimestamp(raw_ms / 1000, timezone.utc)) if isinstance(raw_ms, (int, float)) else None
        raw_updated_ms = props.get("updated")
        updated_at = iso_utc(datetime.fromtimestamp(raw_updated_ms / 1000, timezone.utc)) if isinstance(raw_updated_ms, (int, float)) else None
        coordinates = row.get("geometry", {}).get("coordinates", [])
        longitude = coordinates[0] if len(coordinates) > 0 else None
        latitude = coordinates[1] if len(coordinates) > 1 else None
        depth_km = coordinates[2] if len(coordinates) > 2 else None
        structured_data = {
            "event_id": row.get("id"),
            "magnitude": props.get("mag"),
            "magnitude_type": props.get("magType"),
            "place": props.get("place"),
            "event_type": props.get("type"),
            "review_status": props.get("status"),
            "updated_at": updated_at,
            "longitude": longitude,
            "latitude": latitude,
            "depth_km": depth_km,
            "felt_reports": props.get("felt"),
            "community_intensity": props.get("cdi"),
            "estimated_intensity": props.get("mmi"),
            "alert": props.get("alert"),
            "tsunami_flag": props.get("tsunami"),
            "significance": props.get("sig"),
        }
        result.append({
            "title": str(props.get("title") or "USGS earthquake event"),
            "url": str(props.get("url") or ""),
            "published_at": published_at,
            "source_updated_at": updated_at,
            "time_precision": "datetime" if published_at else "unknown",
            "original_time": str(raw_ms) if raw_ms is not None else None,
            "feed_excerpt": json.dumps(structured_data, ensure_ascii=False, separators=(",", ":")),
            "structured_data": structured_data,
        })
    return result


def parse_ics_time(raw: str, timezone_name: str | None) -> tuple[str | None, str]:
    value = raw.strip()
    if len(value) == 8 and value.isdigit():
        return datetime.strptime(value, "%Y%m%d").date().isoformat(), "date"
    for pattern in ("%Y%m%dT%H%M%SZ", "%Y%m%dT%H%M%S", "%Y%m%dT%H%M"):
        try:
            parsed = datetime.strptime(value, pattern)
        except ValueError:
            continue
        if value.endswith("Z"):
            parsed = parsed.replace(tzinfo=timezone.utc)
        elif timezone_name:
            parsed = parsed.replace(tzinfo=ZoneInfo(timezone_name))
        else:
            return None, "unknown"
        return iso_utc(parsed), "datetime"
    return None, "unknown"


def parse_ics_candidates(payload: bytes, max_items: int, excerpt_chars: int) -> list[dict[str, Any]]:
    text = payload.decode("utf-8-sig", errors="replace").replace("\r\n", "\n")
    unfolded = re.sub(r"\n[ \t]", "", text)
    events = re.findall(r"BEGIN:VEVENT\n(.*?)\nEND:VEVENT", unfolded, flags=re.DOTALL | re.IGNORECASE)
    result: list[dict[str, Any]] = []
    for block in events[:max_items]:
        fields: dict[str, tuple[str, str | None]] = {}
        for line in block.splitlines():
            if ":" not in line:
                continue
            head, value = line.split(":", 1)
            name, *params = head.split(";")
            tzid = next((part.split("=", 1)[1] for part in params if part.upper().startswith("TZID=")), None)
            fields[name.upper()] = (value.replace("\\n", " ").replace("\\,", ","), tzid)
        raw_start, tzid = fields.get("DTSTART", ("", None))
        start, precision = parse_ics_time(raw_start, tzid)
        result.append({
            "title": fields.get("SUMMARY", ("（无标题）", None))[0],
            "url": fields.get("URL", ("", None))[0],
            "published_at": start,
            "time_precision": precision,
            "original_time": raw_start or None,
            "feed_excerpt": plain_text(fields.get("DESCRIPTION", ("", None))[0], excerpt_chars),
        })
    return result


def validate_public_http_url(url: str, allowed_hosts: set[str] | None = None) -> None:
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
        raise ValueError("URL must be a credential-free HTTP(S) URL")
    host = parts.hostname.casefold().rstrip(".")
    if allowed_hosts is not None and host not in {value.casefold().rstrip(".") for value in allowed_hosts}:
        raise ValueError("URL host is outside the configured allowlist")
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(host, parts.port or (443 if parts.scheme == "https" else 80), type=socket.SOCK_STREAM)}
    except socket.gaierror as exc:
        raise ValueError(f"URL host cannot be resolved: {host}") from exc
    for raw in addresses:
        address = ipaddress.ip_address(raw)
        if address.is_private or address.is_loopback or address.is_link_local or address.is_multicast or address.is_reserved or address.is_unspecified:
            raise ValueError("URL resolves to a non-public network address")


class PublicRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, allowed_hosts: set[str] | None = None) -> None:
        super().__init__()
        self.allowed_hosts = allowed_hosts

    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> Any:
        validate_public_http_url(newurl, self.allowed_hosts)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def request_with_retry(
    url: str,
    settings: dict[str, Any],
    accept: str = "application/rss+xml, application/atom+xml, application/json, text/xml;q=0.9",
    allowed_hosts: set[str] | None = None,
) -> tuple[bytes, int, str, int]:
    retries = int(settings.get("retries", 2))
    retry_statuses = {int(value) for value in settings.get("retry_statuses", [])}
    max_bytes = int(settings["max_response_bytes"])
    user_agent = str(settings["user_agent"])
    validate_public_http_url(url, allowed_hosts)
    opener = urllib.request.build_opener(PublicRedirectHandler(allowed_hosts))
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        request = urllib.request.Request(url, headers={"User-Agent": user_agent, "Accept": accept})
        try:
            with opener.open(request, timeout=int(settings["fetch_timeout_seconds"])) as response:
                payload = response.read(max_bytes + 1)
                if len(payload) > max_bytes:
                    raise ValueError(f"response exceeded {max_bytes} bytes")
                return payload, int(response.status), response.headers.get("Content-Type", ""), attempt
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code not in retry_statuses or attempt >= retries:
                raise
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            delay = min(30, int(retry_after)) if retry_after and retry_after.isdigit() else int(settings["retry_backoff_seconds"]) * (2 ** attempt)
            exc.close()
            time.sleep(delay)
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = exc
            if attempt >= retries:
                raise
            time.sleep(int(settings["retry_backoff_seconds"]) * (2 ** attempt))
    raise RuntimeError(str(last_error or "request failed"))


def in_fresh_window(item: dict[str, Any], as_of: datetime, hours: int) -> bool:
    value = item.get("published_at")
    if not value or item.get("time_precision") != "datetime":
        return False
    try:
        published = datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return False
    return as_of - timedelta(hours=hours) <= published <= as_of + timedelta(minutes=5)


def in_future_window(item: dict[str, Any], as_of: datetime, hours: int) -> bool:
    value = item.get("published_at")
    if not value or item.get("time_precision") != "datetime":
        return False
    try:
        event_time = datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return False
    return as_of <= event_time <= as_of + timedelta(hours=hours)


def _article_text(url: str, source: dict[str, Any], settings: dict[str, Any]) -> str:
    if not (source.get("article_fetch") and source.get("rights_review") == "approved"):
        return ""
    article_host = urlsplit(url).hostname
    allowed_hosts = {str(host).casefold() for host in source.get("allowed_article_hosts", [])}
    if not article_host or article_host.casefold() not in allowed_hosts:
        return ""
    robots_url = urljoin(url, "/robots.txt")
    robots = urllib.robotparser.RobotFileParser()
    try:
        robots_payload, _, _, _ = request_with_retry(robots_url, settings, accept="text/plain", allowed_hosts=allowed_hosts)
        robots.parse(robots_payload.decode("utf-8", errors="replace").splitlines())
    except (OSError, ValueError, urllib.error.URLError):
        return ""
    if not robots.can_fetch(str(settings["user_agent"]), url):
        return ""
    payload, _, _, _ = request_with_retry(url, settings, accept="text/html,application/xhtml+xml", allowed_hosts=allowed_hosts)
    return plain_text(payload.decode("utf-8", errors="replace"), int(settings["max_article_chars"]))


def collect_sources(
    source_config: dict[str, Any],
    catalog: dict[str, dict[str, Any]],
    pipeline_config: dict[str, Any],
    as_of: datetime | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    settings = pipeline_config["collection"]
    now = (as_of or datetime.now(timezone.utc)).astimezone(timezone.utc)
    contact_env = str(settings.get("contact_url_env") or "")
    contact = __import__("os").environ.get(contact_env, "") if contact_env else ""
    if contact:
        settings = dict(settings)
        settings["user_agent"] = str(settings["user_agent"]).replace("+public contact URL required before activation", contact)
    candidates: list[dict[str, Any]] = []
    health: list[dict[str, Any]] = []
    for source in source_config.get("sources", []):
        source_id = str(source["id"])
        row = {
            "source_id": source_id, "source": catalog[source_id]["name"], "url": source["url"],
            "interface_type": source["interface_type"], "status": "failed", "attempts": 0,
            "items_seen": 0, "fresh_items": 0, "parsed_date_count": 0, "latest_item_time": None,
            "rights_review": source.get("rights_review"), "allow_public_summary": bool(source.get("allow_public_summary")),
            "reason": None,
        }
        try:
            payload, status, content_type, retries_used = request_with_retry(str(source["url"]), settings)
            row.update({"http_status": status, "content_type": content_type, "attempts": retries_used + 1})
            if source["interface_type"] in {"rss", "atom"}:
                parsed = parse_rss_candidates(payload, int(settings["max_feed_items_per_source"]), int(settings["max_article_chars"]))
            elif source["interface_type"] == "usgs_geojson":
                parsed = parse_usgs_candidates(payload, int(settings["max_feed_items_per_source"]))
            else:
                raise ValueError(f"unsupported interface: {source['interface_type']}")
            row["items_seen"] = len(parsed)
            dated = [item["published_at"] for item in parsed if item.get("published_at")]
            row["parsed_date_count"] = len(dated)
            row["latest_item_time"] = max(dated) if dated else None
            for raw in parsed:
                if not in_fresh_window(raw, now, int(settings["fresh_hours"])):
                    continue
                evidence_text = ""
                if source.get("rights_review") == "approved" and source.get("allow_substantive_analysis"):
                    if source.get("content_mode") in {"feed_excerpt", "structured_public_data"}:
                        evidence_text = raw.get("feed_excerpt", "")
                    elif source.get("content_mode") == "article_html":
                        evidence_text = _article_text(str(raw.get("url") or ""), source, settings)
                meta = catalog[source_id]
                candidates.append({
                    **{key: raw.get(key) for key in ("title", "url", "published_at", "source_updated_at", "time_precision", "original_time", "structured_data")},
                    "source_id": source_id,
                    "source": meta["name"],
                    # Article-level tags are assigned from evidence by the
                    # structured analyzer or the model, never from a publisher's
                    # general coverage profile.
                    "regions": [],
                    "topics": [],
                    "upstream_origin": meta.get("publisher_group_hint") or source_id,
                    "independence_group": meta.get("publisher_group_hint") or source_id,
                    "source_role": source.get("role"),
                    "rights_review": source.get("rights_review"),
                    "allow_public_summary": bool(source.get("allow_public_summary")),
                    "license_url": source.get("terms_url"),
                    "evidence_text": evidence_text,
                    "access_level": "authorized_feed" if evidence_text else "metadata_only",
                })
            row["fresh_items"] = sum(1 for item in candidates if item["source_id"] == source_id)
            row["status"] = "ok" if parsed else "empty"
        except urllib.error.HTTPError as exc:
            row["http_status"] = exc.code
            row["attempts"] = int(settings.get("retries", 2)) + 1 if exc.code in set(settings.get("retry_statuses", [])) else 1
            row["reason"] = f"HTTP {exc.code}; no bypass attempted"
            row["status"] = "rate_limited" if exc.code == 429 else "permission_required" if exc.code in {401, 403} else "failed"
            exc.close()
        except Exception as exc:  # per-source isolation is deliberate
            row["reason"] = f"{type(exc).__name__}: {exc}"
        health.append(row)
    return candidates, health


def collect_calendar_sources(
    calendar_config: dict[str, Any],
    catalog: dict[str, dict[str, Any]],
    pipeline_config: dict[str, Any],
    as_of: datetime,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    settings = pipeline_config["collection"]
    candidates: list[dict[str, Any]] = []
    health: list[dict[str, Any]] = []
    for source in calendar_config.get("sources", []):
        source_id = str(source["id"])
        row = {
            "source_id": source_id, "source": catalog[source_id]["name"], "url": source["url"],
            "interface_type": source["interface_type"], "status": "failed", "attempts": 0,
            "items_seen": 0, "fresh_items": 0, "parsed_date_count": 0, "latest_item_time": None,
            "rights_review": source.get("rights_review"), "allow_public_summary": bool(source.get("allow_public_summary")), "reason": None,
        }
        if source.get("rights_review") != "approved" or not source.get("allow_public_summary"):
            row["status"] = "not_configured"
            row["reason"] = "official calendar rights/timezone review is not approved"
            health.append(row)
            continue
        try:
            payload, status, content_type, retries_used = request_with_retry(str(source["url"]), settings, accept="text/calendar, application/rss+xml, application/atom+xml")
            row.update({"http_status": status, "content_type": content_type, "attempts": retries_used + 1})
            if source["interface_type"] == "ics":
                parsed = parse_ics_candidates(payload, int(settings["max_feed_items_per_source"]), int(settings["max_article_chars"]))
            elif source["interface_type"] in {"rss", "atom"}:
                parsed = parse_rss_candidates(payload, int(settings["max_feed_items_per_source"]), int(settings["max_article_chars"]))
            else:
                raise ValueError(f"unsupported calendar interface: {source['interface_type']}")
            row["items_seen"] = len(parsed)
            dated = [item["published_at"] for item in parsed if item.get("published_at")]
            row["parsed_date_count"] = len(dated)
            row["latest_item_time"] = max(dated) if dated else None
            before = len(candidates)
            for raw in parsed:
                if not in_future_window(raw, as_of, 24):
                    continue
                meta = catalog[source_id]
                candidates.append({
                    **{key: raw.get(key) for key in ("title", "url", "published_at", "time_precision", "original_time")},
                    "source_id": source_id, "source": meta["name"], "regions": meta.get("suggested_regions", ["global"]),
                    "topics": meta.get("suggested_topics", ["politics"]), "upstream_origin": source_id,
                    "independence_group": source_id, "source_role": "official_calendar", "rights_review": "approved",
                    "allow_public_summary": True, "evidence_text": raw.get("feed_excerpt") or raw.get("title"), "access_level": "authorized_feed",
                })
            row["fresh_items"] = len(candidates) - before
            row["status"] = "ok" if parsed else "empty"
        except urllib.error.HTTPError as exc:
            row["http_status"] = exc.code
            row["reason"] = f"HTTP {exc.code}; no bypass attempted"
            row["status"] = "rate_limited" if exc.code == 429 else "permission_required" if exc.code in {401, 403} else "failed"
            exc.close()
        except Exception as exc:
            row["reason"] = f"{type(exc).__name__}: {exc}"
        health.append(row)
    return candidates, health
