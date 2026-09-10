"""Deterministic Chinese briefs for approved structured public data.

This module does not translate prose or invent reporting. It only turns fields
from explicitly supported public datasets into bounded, attributed Chinese
facts and a clearly labelled rule-based interpretation.
"""

from __future__ import annotations

import json
import re
from typing import Any


DIRECTION_ZH = {
    "N": "北", "NNE": "北偏东北", "NE": "东北", "ENE": "东偏东北",
    "E": "东", "ESE": "东偏东南", "SE": "东南", "SSE": "南偏东南",
    "S": "南", "SSW": "南偏西南", "SW": "西南", "WSW": "西偏西南",
    "W": "西", "WNW": "西偏西北", "NW": "西北", "NNW": "北偏西北",
}

COUNTRY_ZH = {
    "Timor Leste": ("东帝汶", "southeast_asia"),
    "Indonesia": ("印度尼西亚", "southeast_asia"),
    "Philippines": ("菲律宾", "southeast_asia"),
    "Myanmar": ("缅甸", "southeast_asia"),
    "Japan": ("日本", "east_asia"),
    "Taiwan": ("中国台湾", "east_asia"),
    "China": ("中国", "east_asia"),
    "Guam": ("关岛", "oceania_pacific"),
    "Fiji": ("斐济", "oceania_pacific"),
    "Tonga": ("汤加", "oceania_pacific"),
    "Vanuatu": ("瓦努阿图", "oceania_pacific"),
    "New Zealand": ("新西兰", "oceania_pacific"),
    "Papua New Guinea": ("巴布亚新几内亚", "oceania_pacific"),
    "Australia": ("澳大利亚", "oceania_pacific"),
    "Mexico": ("墨西哥", "north_america"),
    "United States": ("美国", "north_america"),
    "Canada": ("加拿大", "north_america"),
    "Peru": ("秘鲁", "latin_america_caribbean"),
    "Bolivia": ("玻利维亚", "latin_america_caribbean"),
    "Chile": ("智利", "latin_america_caribbean"),
    "Argentina": ("阿根廷", "latin_america_caribbean"),
    "Costa Rica": ("哥斯达黎加", "latin_america_caribbean"),
    "Ecuador": ("厄瓜多尔", "latin_america_caribbean"),
    "Colombia": ("哥伦比亚", "latin_america_caribbean"),
    "Afghanistan": ("阿富汗", "south_asia"),
    "Pakistan": ("巴基斯坦", "south_asia"),
    "India": ("印度", "south_asia"),
    "Nepal": ("尼泊尔", "south_asia"),
    "Iran": ("伊朗", "mena"),
    "Turkey": ("土耳其", "mena"),
    "Greece": ("希腊", "europe_russia"),
    "Italy": ("意大利", "europe_russia"),
    "Russia": ("俄罗斯", "europe_russia"),
}

NAMED_PLACES_ZH = {
    "southern East Pacific Rise": "南部东太平洋海隆",
    "northern Mid-Atlantic Ridge": "北部大西洋中脊",
    "central Mid-Atlantic Ridge": "中部大西洋中脊",
    "south of the Fiji Islands": "斐济群岛以南海域",
}


def _number(value: Any, digits: int = 1) -> str:
    if not isinstance(value, (int, float)):
        return "未知"
    rendered = f"{float(value):.{digits}f}"
    return rendered.rstrip("0").rstrip(".")


def translate_place(place: str) -> tuple[str, str]:
    raw = (place or "位置未注明").strip()
    if raw in NAMED_PLACES_ZH:
        return NAMED_PLACES_ZH[raw], "oceania_pacific"
    match = re.fullmatch(r"(\d+) km ([A-Z]{1,3}) of (.+?)(?:, ([^,]+))?", raw)
    if match:
        distance, direction, locality, country = match.groups()
        country_zh, region = COUNTRY_ZH.get(country or "", (country or "", "global"))
        prefix = f"{country_zh} " if country_zh else ""
        direction_zh = DIRECTION_ZH.get(direction, direction)
        return f"{prefix}{locality}以{direction_zh}{distance}公里", region
    for name, (translated, region) in COUNTRY_ZH.items():
        if name.casefold() in raw.casefold():
            return re.sub(re.escape(name), translated, raw, flags=re.IGNORECASE), region
    return raw, "global"


def country_from_place(place: str) -> str | None:
    for name, (translated, _) in COUNTRY_ZH.items():
        if name.casefold() in (place or "").casefold():
            return translated
    return None


def region_from_coordinates(longitude: Any, latitude: Any) -> str:
    if not isinstance(longitude, (int, float)) or not isinstance(latitude, (int, float)):
        return "global"
    lon, lat = float(longitude), float(latitude)
    if -170 <= lon <= -50 and lat >= 15:
        return "north_america"
    if -120 <= lon <= -30 and lat < 15:
        return "latin_america_caribbean"
    if -25 <= lon <= 60 and lat >= 35:
        return "europe_russia"
    if -20 <= lon <= 80 and 15 <= lat < 35:
        return "mena"
    if -25 <= lon <= 55 and lat < 15:
        return "sub_saharan_africa"
    if 60 <= lon < 100 and 5 <= lat <= 35:
        return "south_asia"
    if 90 <= lon <= 140 and -15 <= lat < 25:
        return "southeast_asia"
    if 100 <= lon <= 150 and 25 <= lat <= 55:
        return "east_asia"
    if (110 <= lon <= 180 and lat < -10) or abs(lon) > 150:
        return "oceania_pacific"
    if -150 <= lon <= -70 and lat < 0:
        return "latin_america_caribbean"
    return "global"


def can_analyze_structured(cluster: dict[str, Any]) -> bool:
    eligible = [
        item for item in cluster.get("items", [])
        if item.get("rights_review") == "approved" and item.get("evidence_text")
    ]
    return len(eligible) == 1 and eligible[0].get("source_id") == "usgs" and isinstance(eligible[0].get("structured_data"), dict)


def analyze_usgs(cluster: dict[str, Any], as_of: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not can_analyze_structured(cluster):
        raise ValueError("cluster is not a supported USGS structured-data event")
    item = next(
        row for row in cluster["items"]
        if row.get("source_id") == "usgs" and isinstance(row.get("structured_data"), dict)
    )
    data = item["structured_data"]
    magnitude = _number(data.get("magnitude"))
    depth = _number(data.get("depth_km"))
    place_zh, named_region = translate_place(str(data.get("place") or ""))
    country = country_from_place(str(data.get("place") or ""))
    region = named_region if named_region != "global" else region_from_coordinates(data.get("longitude"), data.get("latitude"))
    review_status = str(data.get("review_status") or "未注明")
    tsunami = data.get("tsunami_flag")
    tsunami_note = "该数据条目的海啸标记为0" if tsunami == 0 else "该数据条目的海啸标记为1" if tsunami == 1 else "该条目未提供海啸标记"
    title = f"USGS记录：{place_zh}发生{magnitude}级地震"
    summary = (
        f"美国地质调查局（USGS）的过去24小时地震数据记录显示，{place_zh}发生{magnitude}级地震，"
        f"震源深度约{depth}公里，数据审核状态为“{review_status}”。{tsunami_note}。"
        "该记录不包含人员伤亡或设施损失结论。"
    )
    source_id = "src_usgs_1"
    analysis = {
        "title_zh": title,
        "summary_zh": summary,
        "section_id": "headlines" if isinstance(data.get("magnitude"), (int, float)) and float(data["magnitude"]) >= 6 else "society_world",
        "topic_ids": ["disasters", "science"],
        "region_ids": [region],
        "countries": [country] if country else [],
        "material_update": f"USGS过去24小时目录新增或更新了这条{magnitude}级地震记录。",
        "claims": [
            {
                "text_zh": f"USGS记录的地震震级为{magnitude}，位置为{place_zh}。",
                "kind": "fact",
                "source_ids": [source_id],
                "attribution": "美国地质调查局（USGS）",
                "verification_note": "由USGS GeoJSON的 magnitude、place 与坐标字段直接生成。",
            },
            {
                "text_zh": f"该记录给出的震源深度约为{depth}公里，审核状态为“{review_status}”。",
                "kind": "fact",
                "source_ids": [source_id],
                "attribution": "美国地质调查局（USGS）",
                "verification_note": "由USGS GeoJSON的 geometry.coordinates[2] 与 status 字段直接生成。",
            },
            {
                "text_zh": f"{tsunami_note}；这不等同于对所有海啸风险作出独立判断。",
                "kind": "attributed_claim",
                "source_ids": [source_id],
                "attribution": "USGS数据字段",
                "verification_note": "仅陈述数据字段，不外推地方预警机构结论。",
            },
        ],
        "analysis": {
            "why_it_matters": "这是一条可复核的地震监测记录，可用于定位需要继续关注的区域；单条地震目录不能证明已经造成人员伤亡或基础设施损失。",
            "mechanism": "震级描述释放能量的规模，震源深度和距聚居地位置会影响震感与潜在影响，但这些字段不足以单独判断灾情。",
            "affected_groups": f"{place_zh}周边居民、交通与应急部门可能需要关注后续信息；实际影响范围尚无充分证据。",
            "counter_evidence": "USGS会随更多台站数据修订震级、位置和深度；未出现人员报告或警报字段，不能据此断言当地没有影响。",
            "watch_next": "继续核对USGS事件页的参数修订，并等待当地应急部门关于震感、损失或预警的独立信息。",
        },
        "unknowns": ["当地是否有人员伤亡或财产损失", "地方机构是否发布独立预警或情况通报", "震级与震源参数是否会继续修订"],
    }
    source_time_value = item.get("source_updated_at") or item.get("published_at")
    evidence_excerpt = json.dumps(data, ensure_ascii=False, separators=(",", ":"))[:1200]
    sources = [{
        "id": source_id,
        "publisher": "美国地质调查局（USGS）",
        "url": str(item.get("url") or ""),
        "title": str(item.get("title") or "USGS earthquake event"),
        "source_time": {
            "value": source_time_value,
            "precision": "datetime" if source_time_value else "unknown",
            "original_text": item.get("original_time"),
            "timezone": "UTC" if source_time_value else None,
        },
        "access_level": "authorized_feed",
        "upstream_origin": "usgs",
        "independence_group": "usgs",
        "retrieved_at": as_of,
        "evidence_method": "USGS GeoJSON字段抽取",
        "evidence_excerpt": evidence_excerpt,
        "attribution": "U.S. Geological Survey",
        "license_url": "https://www.usgs.gov/data-management/data-licensing",
    }]
    return analysis, sources
