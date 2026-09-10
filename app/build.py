"""Build a local-only static metadata preview."""

from __future__ import annotations

import argparse
import html
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import jsonschema

if __package__:
    from .analyze import preview_analysis
    from .archive import history_index
    from .collect import load_latest_items
    from .common import CONFIG_DIR, ROOT, SITE_DIR, load_json, load_yaml, write_json
    from .deduplicate import deduplicate
    from .normalize import canonical_url, event_id, sortable_time, time_object
    from .validate import validate_configuration
    from .verify import validate_claim_sources
else:
    # Preserve the documented `python app/build.py` entry point as well as
    # package execution (`python -m app.build`).
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from app.analyze import preview_analysis
    from app.archive import history_index
    from app.collect import load_latest_items
    from app.common import CONFIG_DIR, ROOT, SITE_DIR, load_json, load_yaml, write_json
    from app.deduplicate import deduplicate
    from app.normalize import canonical_url, event_id, sortable_time, time_object
    from app.validate import validate_configuration
    from app.verify import validate_claim_sources


SECTION_KEYWORDS = {
    "science_technology": (" ai ", "artificial intelligence", "space", "satellite", "science", "robot", "technology", "chip"),
    "business": ("economy", "economic", "company", "business", "market", "bank", "inflation", "rate", "trade", "tariff"),
    "society_world": ("climate", "health", "wildlife", "forest", "ocean", "culture", "film", "school", "storm", "flood", "fire"),
}


def e(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def display_beijing(value: str | None, precision: str) -> str:
    if not value:
        return "时间未知"
    if precision == "date" or len(value) == 10:
        return f"{value}（仅日期）"
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        shown = parsed.astimezone(ZoneInfo("Asia/Shanghai"))
        return shown.strftime("%m月%d日 %H:%M 北京时间")
    except ValueError:
        return value


def classify(title: str, source_id: str) -> str | None:
    if source_id == "fed":
        return "business"
    if source_id == "mongabay":
        return "society_world"
    padded = f" {title.casefold()} "
    for section, keywords in SECTION_KEYWORDS.items():
        if any(keyword in padded for keyword in keywords):
            return section
    return None


def choose_items(items: list[dict[str, Any]]) -> list[tuple[dict[str, Any], str]]:
    unique = deduplicate(items)
    selected: list[tuple[dict[str, Any], str]] = []
    used_ids: set[str] = set()
    headline_sources: set[str] = set()
    for item in unique:
        source_id = str(item.get("source_id"))
        if source_id in headline_sources:
            continue
        selected.append((item, "headlines"))
        used_ids.add(event_id(item))
        headline_sources.add(source_id)
        if len(headline_sources) == 5:
            break

    counts = {"science_technology": 0, "business": 0, "society_world": 0}
    for item in unique:
        if event_id(item) in used_ids:
            continue
        section = classify(str(item.get("title", "")), str(item.get("source_id", "")))
        if section and counts[section] < 3:
            selected.append((item, section))
            used_ids.add(event_id(item))
            counts[section] += 1
        if all(count == 3 for count in counts.values()):
            break
    return selected


def make_event(item: dict[str, Any], section_id: str, as_of: str) -> dict[str, Any]:
    identifier = event_id(item)
    source_record_id = f"src_{identifier[4:]}"
    source_name = str(item.get("source") or item.get("source_id") or "未知来源")
    title = str(item.get("title") or "（无标题）")
    analysis, _ = preview_analysis()
    source_time = time_object(item)
    return {
        "event_id": identifier,
        "title_zh": title,
        "topic_ids": list(dict.fromkeys(item.get("topics") or ["society"])),
        "region_ids": list(dict.fromkeys(item.get("regions") or ["global"])),
        "countries": [],
        "section_id": section_id,
        "event_time": dict(source_time),
        "publication_time": dict(source_time),
        "fetched_at": as_of,
        "as_of": as_of,
        "material_update": "首次出现在本次元数据抓取中；正文内容尚未核验。",
        "summary_zh": f"{source_name} 的订阅源列出了这一标题。本地预览仅保留标题、链接和时间，未抓取正文，也未生成新闻摘要。",
        "claims": [{
            "id": f"claim_{identifier[4:]}",
            "text_zh": f"{source_name} 的订阅源在所示时间列出了这一标题。",
            "kind": "attributed_claim",
            "source_ids": [source_record_id],
            "attribution": source_name,
            "verification_note": "仅核对订阅源元数据；未将标题内容视为已独立证实的事实。",
        }],
        "sources": [{
            "id": source_record_id,
            "publisher": source_name,
            "url": canonical_url(str(item.get("url") or "")),
            "title": title,
            "source_time": dict(source_time),
            "access_level": "metadata_only",
            "upstream_origin": item.get("upstream_origin"),
            "independence_group": item.get("independence_group"),
            "retrieved_at": as_of,
        }],
        "evidence_status": "attributed_report",
        "analysis": analysis,
        "unknowns": ["正文内容未抓取", "核心主张未独立核验", "事件国家未进行实体识别"],
        "corrections": [],
        "human_review_required": True,
    }


def event_cards(events: list[dict[str, Any]], topic_names: dict[str, str], region_names: dict[str, str]) -> str:
    cards: list[str] = []
    for event in events:
        source = event["sources"][0]
        publication_time = event["publication_time"]
        shown_time = display_beijing(publication_time.get("value"), str(publication_time.get("precision")))
        topics = " ".join(e(topic_names.get(item, item)) for item in event["topic_ids"][:2])
        region_labels = [region_names.get(item, "全球") if item != "global" else "全球" for item in event["region_ids"][:2]]
        regions = " · ".join(e(item) for item in region_labels)
        status_label = "待人工复核" if event.get("human_review_required") else "证据闸门已通过"
        cards.append(f"""
        <article class="story-card" data-event-id="{e(event['event_id'])}" data-section="{e(event['section_id'])}" data-topics="{e(' '.join(event['topic_ids']))}" data-regions="{e(' '.join(event['region_ids']))}">
          <div class="card-kicker"><span>{e(source['publisher'])}</span><time>{e(shown_time)}</time></div>
          <h3>{e(event['title_zh'])}</h3>
          <p>{e(event['summary_zh'])}</p>
          <div class="tag-row"><span>{regions}</span><span>{topics}</span></div>
          <div class="card-footer"><span class="status-dot">{e(event['evidence_status'])} · {e(status_label)}</span><button class="evidence-button" type="button" data-open-evidence="{e(event['event_id'])}">查看证据</button></div>
        </article>""")
    return "\n".join(cards)


def section_markup(site_config: dict[str, Any], events: list[dict[str, Any]], cards_html: str) -> str:
    by_section: dict[str, int] = {}
    for event in events:
        by_section[event["section_id"]] = by_section.get(event["section_id"], 0) + 1
    sections: list[str] = []
    for index, section in enumerate(site_config["sections"], start=1):
        count = by_section.get(section["id"], 0)
        empty = "" if count else '<div class="empty-state">本次元数据预览没有达到该栏目的证据门槛；不为填满版面而生成内容。</div>'
        sections.append(f"""
        <section class="digest-section" id="section-{e(section['id'])}" data-section-panel="{e(section['id'])}">
          <header class="section-header"><span class="section-number">{index:02d}</span><div><h2>{e(section['name'])}</h2><p>{e(section['rule'])}</p></div><span class="section-count">{count} 条</span></header>
          <div class="story-grid" data-cards-for="{e(section['id'])}"></div>{empty}
        </section>""")
    return "\n".join(sections) + f'<template id="story-cards-template">{cards_html}</template>'


def filter_buttons(items: list[dict[str, Any]], kind: str) -> str:
    buttons = [f'<button type="button" class="filter-chip is-active" data-filter-{kind}="all">全部</button>']
    for item in items:
        buttons.append(f'<button type="button" class="filter-chip" data-filter-{kind}="{e(item["id"])}">{e(item["name"])}</button>')
    return "".join(buttons)


def page_shell(title: str, body: str, active: str, as_of: str, production: bool = False) -> str:
    nav = "".join(
        f'<a class="{"is-active" if key == active else ""}" href="{href}">{label}</a>'
        for key, href, label in [
            ("digest", "index.html", "今日雷达"),
            ("health", "source-health.html", "来源健康"),
            ("history", "history.html", "历史记录"),
            ("corrections", "corrections.html", "更正日志"),
        ]
    )
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow"><meta name="color-scheme" content="light">
<title>{e(title)} · 世界观察</title><link rel="stylesheet" href="assets/styles.css"></head>
<body><div class="preview-banner">{'EVIDENCE-GATED DAILY EDITION · 证据不足不发布' if production else 'LOCAL PREVIEW · 开发预览，不是已发布新闻'}</div><div id="stale-warning" class="stale-warning" hidden>本页数据已经过期，当前展示的是最近一次成功版本。</div>
<header class="site-header"><a class="brand" href="index.html"><span class="brand-mark">世</span><span><strong>世界观察</strong><small>GLOBAL NEWS RADAR</small></span></a><nav>{nav}</nav></header>
<main>{body}</main><footer><p>本地静态预览 · 不含模型生成分析 · 不含正文转载 · 不使用浏览器 Cookie</p><p>数据截止：{e(as_of)} UTC</p></footer>
<script>window.NEWS_AS_OF={json.dumps(as_of)};window.NEWS_STALE_HOURS=30;</script><script src="assets/app.js" defer></script></body></html>"""


def health_page(health: dict[str, Any], production: bool = False) -> str:
    labels = {"ok": "正常", "empty": "合法空列表", "partial": "部分可用", "not_configured": "未配置", "permission_required": "需许可", "stale": "已过期", "failed": "失败"}
    rows = []
    for item in health["sources"]:
        rows.append(f"<tr><td><a href=\"{e(item['url'])}\" target=\"_blank\" rel=\"noopener noreferrer\">{e(item['source'])}</a></td><td><span class=\"health health-{e(item['status'])}\">{e(labels.get(item['status'], item['status']))}</span></td><td>{e(item.get('http_status') or '—')}</td><td>{e(item['item_count'])}</td><td>{e(item.get('latest_item_time') or '—')}</td><td>{e(item.get('failure_reason') or item['license_status'])}</td></tr>")
    summary = health["summary"]
    body = f"""<section class="page-hero compact"><p class="eyebrow">SOURCE DIAGNOSTICS</p><h1>来源健康检查</h1><p>这是接口响应与格式检查，不是转载许可，也不把公开网页等同于可用接口。</p></section>
    <section class="metric-strip"><div><strong>{summary['total']}</strong><span>已检查</span></div><div><strong>{summary['preview_eligible']}</strong><span>可供本地预览</span></div><div><strong>{summary['ok']}</strong><span>接口正常</span></div><div><strong>{summary['failed']}</strong><span>请求失败</span></div></section>
    <section class="table-panel"><div class="table-scroll"><table><thead><tr><th>来源</th><th>状态</th><th>HTTP</th><th>条目</th><th>最新时间（UTC）</th><th>边界 / 原因</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div></section>
    <aside class="method-note"><strong>本轮边界</strong><p>GDELT 的 429 未重试绕过；NASA 的 403 未绕过；ReliefWeb 缺少预批准 appname 因而未请求；UNESCO 需要书面许可，未进入卡片；没有社交平台或浏览器会话参与。</p></aside>"""
    return page_shell("来源健康", body, "health", health["tested_at"], production)


def build(production: bool = False) -> dict[str, Any]:
    validation = validate_configuration()
    site_config = load_yaml(CONFIG_DIR / "site.yaml")
    health = load_json(ROOT / "source-health.json")
    if production:
        bundle = load_json(ROOT / "state" / "publishable-events.json")
        if bundle.get("publication_mode") != "production":
            raise ValueError("publishable-events.json is not a production bundle")
        as_of = str(bundle["as_of"])
        events = list(bundle.get("events", []))
        if not events:
            raise ValueError("production build requires at least one event")
    else:
        as_of, raw_items = load_latest_items()
        selected = choose_items(raw_items)
        events = [make_event(item, section, as_of) for item, section in selected]

    schema = load_json(ROOT / "schemas" / "event.schema.json")
    for event in events:
        jsonschema.validate(event, schema)
        errors = validate_claim_sources(event)
        if errors:
            raise ValueError("; ".join(errors))

    SITE_DIR.mkdir(parents=True, exist_ok=True)
    (SITE_DIR / "assets").mkdir(parents=True, exist_ok=True)
    write_json(ROOT / "state" / "events.json", {"schema_version": "1.0", "publication_mode": "production" if production else "preview", "as_of": as_of, "events": events})
    pipeline_config = load_yaml(CONFIG_DIR / "pipeline.yaml")
    write_json(SITE_DIR / "data.json", {"as_of": as_of, "publication_mode": "production" if production else "preview", "events": events, "source_health": health["summary"], "stale_after_hours": pipeline_config["publication"]["stale_after_hours"]})
    shutil.copy2(ROOT / "assets" / "styles.css", SITE_DIR / "assets" / "styles.css")
    shutil.copy2(ROOT / "assets" / "app.js", SITE_DIR / "assets" / "app.js")

    topic_names = {row["id"]: row["name"] for row in site_config["coverage"]["topics"]}
    region_names = {row["id"]: row["name"] for row in site_config["coverage"]["regions"]}
    cards = event_cards(events, topic_names, region_names)
    hero_kicker = "DAILY EDITION · EVIDENCE GATED" if production else "DAILY SIGNAL · METADATA PREVIEW"
    hero_copy = "每条主张绑定来源，只有通过许可、24小时、去重、独立证据和结构化分析校验的事件才会出现。" if production else "把来源健康、证据状态与覆盖缺口放在新闻标题之前。当前只显示公开订阅源的元数据样本。"
    body = f"""<section class="page-hero"><div><p class="eyebrow">{hero_kicker}</p><h1>看见世界，<br><em>不抢跑结论。</em></h1><p class="hero-copy">{hero_copy}</p></div><div class="hero-status"><span>AS OF · UTC</span><strong>{e(as_of.replace('T', ' ')[:16])}</strong><p>北京时间 {e(display_beijing(as_of, 'datetime'))}</p><a href="source-health.html">查看完整来源诊断 →</a></div></section>
    <section class="status-ribbon"><div><span class="pulse"></span><strong>{health['summary']['preview_eligible']} 个</strong> 可预览来源</div><div><strong>{len(events)} 条</strong> 元数据样本</div><div><strong>0 条</strong> 模型生成分析</div><div><strong>未发布</strong> 本地状态</div></section>
    <section class="filter-panel"><div><span class="filter-label">议题</span><div class="chip-row" data-topic-filters>{filter_buttons(site_config['coverage']['topics'], 'topic')}</div></div><div><span class="filter-label">地区</span><div class="chip-row" data-region-filters>{filter_buttons(site_config['coverage']['regions'], 'region')}</div></div><p class="filter-note">标签来自候选源覆盖配置，仅用于浏览，不代表已完成文章级分类。</p></section>
    <div class="digest-layout"><aside class="section-index"><span>今日目录</span>{''.join(f'<a href="#section-{e(row["id"])}"><b>{i:02d}</b>{e(row["name"])}</a>' for i, row in enumerate(site_config['sections'], 1))}</aside><div class="digest-content">{section_markup(site_config, events, cards)}</div></div>
    <section class="methodology"><p class="eyebrow">METHOD NOTE</p><h2>这份版本刻意保留了“空白”</h2><div><p>{'AI 只接收许可范围内的证据包，输出必须通过 JSON Schema 与逐条来源引用校验；证据不足的候选不会进入站点。' if production else '没有模型配置，因此不翻译、不改写、不补背景；标题内容不自动升级为事实。证据不足的暗线、争议、更正与未来日历均保持空栏。'}</p><p>转载媒体不增加独立证据数；日期缺少时分秒就不虚构时间；接口失败只代表本轮覆盖缺口，不代表当地没有新闻。</p></div></section>
    <dialog id="evidence-dialog"><button class="dialog-close" type="button" aria-label="关闭">×</button><div id="evidence-content"></div></dialog>"""
    (SITE_DIR / "index.html").write_text(page_shell("今日雷达", body, "digest", as_of, production), encoding="utf-8", newline="\n")
    (SITE_DIR / "source-health.html").write_text(health_page(health, production).replace("window.NEWS_STALE_HOURS=30", f"window.NEWS_STALE_HOURS={int(pipeline_config['publication']['stale_after_hours'])}"), encoding="utf-8", newline="\n")
    history = history_index(str(pipeline_config["archive"]["directory"]))
    history_rows = "".join(f'<article class="archive-row"><time>{e(row.get("as_of"))}</time><strong>{e(row.get("event_count"))} 条</strong><span>{e("；".join(row.get("coverage", {}).get("gaps", [])) or "覆盖目标已达到")}</span></article>' for row in history)
    history_content = history_rows or '<section class="empty-page"><span>01</span><h2>暂无历史版本</h2><p>只有完整通过发布闸门的版本才会进入归档。</p></section>'
    history_body = f'<section class="page-hero compact"><p class="eyebrow">ARCHIVE</p><h1>历史记录</h1><p>每个快照保留原始 as_of；失败运行不会冒充当天新闻。</p></section><section class="archive-list">{history_content}</section>'
    correction_rows = [correction for event in events for correction in event.get("corrections", [])]
    correction_content = "".join(f'<article class="archive-row"><time>{e(row.get("at"))}</time><strong>{e(row.get("old_claim"))}</strong><span>{e(row.get("new_claim"))} · {e(row.get("reason"))}</span></article>' for row in correction_rows) or '<section class="empty-page"><span>00</span><h2>暂无更正记录</h2><p>更正必须保留旧主张、新主张、原因、时间和证据来源。</p></section>'
    corrections_body = f'<section class="page-hero compact"><p class="eyebrow">CORRECTION LOG</p><h1>更正日志</h1><p>删除不是更正机制；重要指控仍需人工审核。</p></section><section class="archive-list">{correction_content}</section>'
    (SITE_DIR / "history.html").write_text(page_shell("历史记录", history_body, "history", as_of, production), encoding="utf-8", newline="\n")
    (SITE_DIR / "corrections.html").write_text(page_shell("更正日志", corrections_body, "corrections", as_of, production), encoding="utf-8", newline="\n")
    return {"events": len(events), "as_of": as_of, "validation_checks": len(validation["checks"]), "output": str(SITE_DIR)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build the existing static site.")
    parser.add_argument("--production", action="store_true", help="Build only from an evidence-gated publishable bundle.")
    args = parser.parse_args()
    print(json.dumps(build(production=args.production), ensure_ascii=False, indent=2))
