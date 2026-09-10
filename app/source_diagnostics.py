from __future__ import annotations

import argparse
import json
import os
import socket
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
USER_AGENT = "GlobalNewsRadar/0.1 (local source diagnostics; metadata only)"


@dataclass
class ParsedItem:
    title: str
    url: str
    published_at: str | None
    time_precision: str
    original_time: str | None


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_time(raw: Any) -> tuple[str | None, str]:
    if raw is None:
        return None, "unknown"
    text = str(raw).strip()
    if not text:
        return None, "unknown"
    try:
        value = parsedate_to_datetime(text)
        if value.tzinfo is None:
            return None, "unknown"
        return iso_utc(value), "datetime"
    except (TypeError, ValueError, OverflowError):
        pass
    normalized = text.replace("Z", "+00:00")
    try:
        value = datetime.fromisoformat(normalized)
        if value.tzinfo is None:
            if len(text) == 10:
                return text, "date"
            return None, "unknown"
        return iso_utc(value), "datetime"
    except ValueError:
        pass
    if len(text) >= 8 and text[:8].isdigit():
        try:
            value = datetime.strptime(text[:15], "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
            return iso_utc(value), "datetime"
        except ValueError:
            try:
                return datetime.strptime(text[:8], "%Y%m%d").date().isoformat(), "date"
            except ValueError:
                return None, "unknown"
    return None, "unknown"


def child_text(node: ET.Element, local_names: tuple[str, ...]) -> str | None:
    for child in node.iter():
        local = child.tag.rsplit("}", 1)[-1].lower()
        if local in local_names and child.text and child.text.strip():
            return child.text.strip()
    return None


def feed_link(node: ET.Element) -> str:
    for child in node:
        if child.tag.rsplit("}", 1)[-1].lower() != "link":
            continue
        href = child.attrib.get("href")
        rel = child.attrib.get("rel", "alternate")
        if href and rel in {"alternate", ""}:
            return href.strip()
        if child.text and child.text.strip():
            return child.text.strip()
    return ""


def parse_feed(payload: bytes, max_items: int) -> list[ParsedItem]:
    root = ET.fromstring(payload)
    root_name = root.tag.rsplit("}", 1)[-1].lower()
    if root_name not in {"rss", "feed", "rdf"}:
        raise ValueError(f"unexpected XML root element: {root_name or 'unknown'}")
    entries = [node for node in root.iter() if node.tag.rsplit("}", 1)[-1].lower() in {"item", "entry"}]
    items: list[ParsedItem] = []
    for node in entries[:max_items]:
        title = child_text(node, ("title",)) or "（无标题）"
        link = feed_link(node)
        raw_time = child_text(node, ("pubdate", "published", "updated", "date", "created"))
        parsed, precision = parse_time(raw_time)
        items.append(ParsedItem(title=title, url=link, published_at=parsed, time_precision=precision, original_time=raw_time))
    return items


def parse_json(payload: bytes, interface_type: str, max_items: int) -> list[ParsedItem]:
    data = json.loads(payload.decode("utf-8-sig"))
    items: list[ParsedItem] = []
    if interface_type == "usgs_geojson":
        rows = data.get("features", [])
        for row in rows[:max_items]:
            props = row.get("properties", {})
            raw_ms = props.get("time")
            published = None
            if isinstance(raw_ms, (int, float)):
                published = iso_utc(datetime.fromtimestamp(raw_ms / 1000, tz=timezone.utc))
            items.append(ParsedItem(
                title=str(props.get("title") or "USGS earthquake event"),
                url=str(props.get("url") or ""),
                published_at=published,
                time_precision="datetime" if published else "unknown",
                original_time=str(raw_ms) if raw_ms is not None else None,
            ))
    elif interface_type == "gdelt_json":
        rows = data.get("articles", [])
        for row in rows[:max_items]:
            raw_time = row.get("seendate")
            parsed, precision = parse_time(raw_time)
            items.append(ParsedItem(
                title=str(row.get("title") or "（无标题）"),
                url=str(row.get("url") or ""),
                published_at=parsed,
                time_precision=precision,
                original_time=str(raw_time) if raw_time is not None else None,
            ))
    else:
        raise ValueError(f"unsupported JSON interface: {interface_type}")
    return items


def request_bytes(url: str, timeout: int, max_bytes: int) -> tuple[bytes, int, str]:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/rss+xml, application/atom+xml, application/json, application/geo+json, text/xml;q=0.9, */*;q=0.2",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = response.read(max_bytes + 1)
        if len(payload) > max_bytes:
            raise ValueError(f"response exceeded {max_bytes} bytes")
        return payload, int(response.status), response.headers.get("Content-Type", "")


def latest_time(items: list[ParsedItem]) -> str | None:
    parsed = [item.published_at for item in items if item.published_at]
    return max(parsed) if parsed else None


def is_stale(latest: str | None, tested_at: datetime, days: int = 14) -> bool:
    if not latest or len(latest) == 10:
        return False
    try:
        value = datetime.fromisoformat(latest.replace("Z", "+00:00"))
    except ValueError:
        return False
    return (tested_at - value.astimezone(timezone.utc)).days > days


def diagnose(entry: dict[str, Any], catalog: dict[str, Any], defaults: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    tested_at = utc_now()
    source_id = entry["id"]
    source_meta = catalog[source_id]
    required_env = entry.get("required_env")
    base = {
        "source_id": source_id,
        "source": source_meta["name"],
        "url": entry["url"],
        "interface_type": entry["interface_type"],
        "tested_at": iso_utc(tested_at),
        "status": "failed",
        "http_status": None,
        "content_type": None,
        "item_count": 0,
        "parsed_date_count": 0,
        "latest_item_time": None,
        "license_status": entry["license_status"],
        "documentation_status": entry["documentation_status"],
        "documentation_url": entry["documentation_url"],
        "failure_reason": None,
        "preview_eligible": False,
        "regions": source_meta.get("suggested_regions", []),
        "topics": source_meta.get("suggested_topics", []),
    }
    if required_env and not os.environ.get(required_env):
        base["status"] = "not_configured"
        base["failure_reason"] = f"missing required environment variable: {required_env}"
        return base, []
    if entry.get("documentation_status") == "official_notice_feed_retired":
        base["status"] = "stale"
        base["failure_reason"] = "official notice says this news RSS feed is no longer updated"
        return base, []
    try:
        payload, http_status, content_type = request_bytes(
            entry["url"],
            timeout=int(defaults["timeout_seconds"]),
            max_bytes=int(defaults["max_response_bytes"]),
        )
        base["http_status"] = http_status
        base["content_type"] = content_type
        if entry["interface_type"] in {"rss", "atom"}:
            items = parse_feed(payload, int(defaults["max_items"]))
        elif entry["interface_type"] in {"usgs_geojson", "gdelt_json"}:
            items = parse_json(payload, entry["interface_type"], int(defaults["max_items"]))
        else:
            raise ValueError(f"adapter not available: {entry['interface_type']}")
        base["item_count"] = len(items)
        base["parsed_date_count"] = sum(1 for item in items if item.published_at)
        base["latest_item_time"] = latest_time(items)
        if not items:
            base["status"] = "empty"
        elif is_stale(base["latest_item_time"], tested_at):
            base["status"] = "stale"
            base["failure_reason"] = "latest parsed item is older than 14 days"
        elif base["parsed_date_count"] == 0:
            base["status"] = "partial"
            base["failure_reason"] = "items parsed but no publication dates were usable"
        else:
            base["status"] = "ok"
        base["preview_eligible"] = bool(entry.get("allow_local_preview")) and base["status"] == "ok"
        samples = [
            {
                "source_id": source_id,
                "source": source_meta["name"],
                "title": item.title,
                "url": item.url,
                "published_at": item.published_at,
                "time_precision": item.time_precision,
                "original_time": item.original_time,
                "regions": source_meta.get("suggested_regions", []),
                "topics": source_meta.get("suggested_topics", []),
                "access_level": "metadata_only",
                "upstream_origin": source_meta.get("publisher_group_hint"),
                "independence_group": source_meta.get("publisher_group_hint"),
            }
            for item in items[:5]
        ]
        return base, samples
    except urllib.error.HTTPError as exc:
        base["http_status"] = exc.code
        if exc.code == 429:
            base["status"] = "partial"
            base["failure_reason"] = "HTTP 429 rate limited; no bypass attempted"
        elif exc.code == 404:
            base["status"] = "failed"
            base["failure_reason"] = "HTTP 404 endpoint not found"
        elif exc.code in {401, 403}:
            base["status"] = "permission_required"
            base["failure_reason"] = f"HTTP {exc.code} access denied; no bypass attempted"
        else:
            base["failure_reason"] = f"HTTP {exc.code}"
        exc.close()
    except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
        base["failure_reason"] = f"network error: {getattr(exc, 'reason', exc)}"
    except ET.ParseError as exc:
        base["failure_reason"] = f"invalid RSS/Atom XML: {exc}"
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        base["failure_reason"] = str(exc)
    return base, []


def markdown_report(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# 信息源健康诊断",
        "",
        f"运行时间（UTC）：{report['tested_at']}",
        "",
        "本报告只验证本地环境中的接口响应、格式和日期解析。它不构成转载许可，也不把候选源改为正式启用。测试只保存标题、链接和时间等元数据。",
        "",
        f"- 已测试/检查：{summary['total']} 个",
        f"- 可用于本地元数据预览：{summary['preview_eligible']} 个",
        f"- 正常：{summary['ok']} 个；合法空列表：{summary['empty']} 个；部分可用：{summary['partial']} 个",
        f"- 未配置：{summary['not_configured']} 个；需许可：{summary['permission_required']} 个；过期：{summary['stale']} 个；失败：{summary['failed']} 个",
        "",
        "| 来源 | 接口 | 状态 | HTTP | 条目 | 日期可解析 | 最新时间 | 许可/使用边界 | 失败原因 |",
        "|---|---|---:|---:|---:|---:|---|---|---|",
    ]
    for item in report["sources"]:
        reason = (item["failure_reason"] or "").replace("|", "\\|")
        license_status = item["license_status"].replace("|", "\\|")
        lines.append(
            f"| [{item['source']}]({item['url']}) | {item['interface_type']} | {item['status']} | "
            f"{item['http_status'] or ''} | {item['item_count']} | {item['parsed_date_count']} | "
            f"{item['latest_item_time'] or ''} | {license_status} | {reason} |"
        )
    lines.extend([
        "",
        "## 结论与覆盖盲区",
        "",
        "- 当前预览只展示成功取得且日期可解析的元数据；失败来源不会被解释为对应地区没有新闻。",
        "- 新闻社、媒体和国际组织的公开页面或RSS可访问，不自动意味着允许公开再发布全文或自动生成摘要。",
        "- ReliefWeb 在缺少预批准 appname 时保持 not_configured；UNESCO 因书面许可要求不进入本地新闻卡片。",
        "- GDELT 只用于发现候选，不能作为独立事实核验来源；其条目必须回到真正发布者核对。",
        "- 社交平台、本地浏览器登录和 Cookie 均未参与本次诊断。",
        "",
    ])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnose selected free/public news endpoints.")
    parser.add_argument("--config", default=str(ROOT / "config" / "diagnostic_sources.yaml"))
    parser.add_argument("--offline", action="store_true", help="Validate configuration without making network requests.")
    args = parser.parse_args()

    source_catalog = yaml.safe_load((ROOT / "config" / "sources.yaml").read_text(encoding="utf-8"))
    diagnostic_config = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    catalog = {item["id"]: item for item in source_catalog["sources"]}
    selected = diagnostic_config["sources"]
    missing = [item["id"] for item in selected if item["id"] not in catalog]
    if missing:
        raise SystemExit(f"diagnostic source IDs absent from catalog: {', '.join(missing)}")

    results: list[dict[str, Any]] = []
    samples: list[dict[str, Any]] = []
    if args.offline:
        for entry in selected:
            meta = catalog[entry["id"]]
            results.append({
                "source_id": entry["id"], "source": meta["name"], "url": entry["url"],
                "interface_type": entry["interface_type"], "tested_at": iso_utc(utc_now()),
                "status": "not_configured", "http_status": None, "content_type": None,
                "item_count": 0, "parsed_date_count": 0, "latest_item_time": None,
                "license_status": entry["license_status"], "documentation_status": entry["documentation_status"],
                "documentation_url": entry["documentation_url"], "failure_reason": "offline validation only",
                "preview_eligible": False, "regions": meta.get("suggested_regions", []),
                "topics": meta.get("suggested_topics", []),
            })
    else:
        for entry in selected:
            result, source_samples = diagnose(entry, catalog, diagnostic_config["defaults"])
            results.append(result)
            if result["preview_eligible"]:
                samples.extend(source_samples)

    counts = {status: sum(1 for item in results if item["status"] == status) for status in (
        "ok", "empty", "partial", "not_configured", "permission_required", "stale", "failed"
    )}
    report = {
        "schema_version": "1.0",
        "tested_at": iso_utc(utc_now()),
        "policy": {
            "network_test_only": True,
            "metadata_only": True,
            "browser_cookies_used": False,
            "paid_services_used": False,
            "publication_performed": False,
        },
        "summary": {
            "total": len(results),
            "preview_eligible": sum(1 for item in results if item["preview_eligible"]),
            **counts,
        },
        "sources": results,
    }
    (ROOT / "state").mkdir(exist_ok=True)
    (ROOT / "source-health.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (ROOT / "source-health.md").write_text(markdown_report(report), encoding="utf-8")
    (ROOT / "state" / "latest-items.json").write_text(json.dumps({
        "schema_version": "1.0",
        "as_of": report["tested_at"],
        "content_policy": "metadata_only_no_generated_analysis",
        "items": samples,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
