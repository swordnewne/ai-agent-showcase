# -*- coding: utf-8 -*-
"""news_window.py — 新闻窗口深模块（P0.5 阶段 A-2 影子验证版）。

单一入口 `get_news_window`，内部完成：
- unix 秒/毫秒、ISO、日级精度的发布时间解析（修复 _parse_news_time 只认 ISO）；
- 双条件门禁：采集时间在窗口内 且 发布时间 <= 截止；发布早于窗口起点标 expired；
- 核验标记：仅页面核验通过的源（sina/wsj，见账本 P0.5 A-1 回执）可置 verified；
- 交模型排序：核验状态 → 发布时间倒序 → 来源，不凑 15 条配额（陷阱 3）。

影子阶段自律：本模块暂不被生产入口 import；阶段 B 切换时再 vendoring 契约层
news_evidence_adapter 进生产树并委托其 normalize/dedup。
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

CST = timezone(timedelta(hours=8))

# 2026-10-12 原文页核对通过的源（账本 P0.5 阶段 A-1 回执）；其余源永不置 verified
VERIFIED_SOURCES = frozenset({"sina", "wsj"})

# 病态天护栏：单窗口条目上限（正常日 ~150，周末 ~350）；溢出时截断并记 meta，
# 防源 API 故障刷屏灌爆单次模型请求（2026-10-12 与用户确认的口径）
OVERFLOW_GUARD = 400

_PLAUSIBLE_MIN = datetime(2015, 1, 1, tzinfo=CST)


def parse_publish_time(value):
    """解析发布时间。返回 (datetime|None, precision)，precision ∈ {second, day}。"""
    if value is None:
        return None, None
    text = str(value).strip()
    if not text:
        return None, None
    if text.isdigit():
        num = int(text)
        if num > 10_000_000_000:
            num = num / 1000.0
        try:
            moment = datetime.fromtimestamp(num, tz=CST)
        except (OverflowError, OSError, ValueError):
            return None, None
        if _PLAUSIBLE_MIN <= moment <= datetime.now(CST) + timedelta(days=2):
            return moment, "second"
        return None, None
    # 日级精度必须先于 fromisoformat 判定——Python 会把 "2026-10-12" 解析成当日零点，
    # 若不拦截会被误标为秒级精度（回放验证抓到的实例）
    if len(text) == 10 and text[4] == "-" and text[7] == "-":
        try:
            return datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=CST), "day"
        except ValueError:
            return None, None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=CST)
        return parsed.astimezone(CST), "second"
    except ValueError:
        pass
    for fmt, precision in (("%Y-%m-%d %H:%M:%S", "second"), ("%Y-%m-%d %H:%M", "second"), ("%Y-%m-%d", "day")):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=CST), precision
        except ValueError:
            continue
    return None, None


def _parse_collected(item):
    for field in ("available_at", "collected_at", "collector_time", "time"):
        raw = item.get(field)
        if not raw:
            continue
        try:
            parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            continue
        if parsed.tzinfo is not None:
            return parsed.astimezone(CST)
    return None


def _content_hash(item):
    existing = str(item.get("content_hash") or "").strip()
    if existing:
        return existing
    payload = json.dumps({"title": item.get("title", ""), "intro": item.get("intro", "")},
                         ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _identity(item):
    source = str(item.get("source_id") or item.get("source") or "unknown")
    url = str(item.get("source_url") or item.get("url") or "").strip()
    item_id = str(item.get("source_item_id") or "").strip()
    key = "url:" + url if url else "id:" + item_id if item_id else "title:" + str(item.get("title"))
    return (source, key, _content_hash(item))


def _classify(item, start_at, cutoff_at):
    """双条件门禁。返回 (status, published, precision)。

    status ∈ fresh / expired / time_unknown；日级精度按日期与边界比较（陷阱 2）。
    """
    raw = (item.get("published_at_raw") or item.get("publication_time_raw")
           or item.get("published_at") or item.get("ctime") or item.get("display_time"))
    published, precision = parse_publish_time(raw)
    if published is None:
        return "time_unknown", None, None
    if precision == "day":
        if published.date() > cutoff_at.date():
            return "time_unknown", published, precision  # 未来日期存疑，不硬判过期
        return ("expired" if published.date() < start_at.date() else "fresh"), published, precision
    if published > cutoff_at + timedelta(minutes=5):
        return "time_unknown", published, precision  # 容忍采集端 5 分钟时钟偏差
    return ("expired" if published < start_at else "fresh"), published, precision


def _norm_title(text):
    return re.sub(r"[\s\W_]+", "", str(text or "").lower())


def _bigrams(text):
    norm = _norm_title(text)
    if len(norm) >= 2:
        return {norm[i:i + 2] for i in range(len(norm) - 1)}
    return {norm} if norm else set()


def cross_source_overlap(items, source_a="sina", source_b="cls", threshold=0.6):
    """新浪↔财联社同事件重叠测量（2026-10-13 方案 A：只测量标注，不丢弃）。

    新浪大量转载财联社电报；标题 char-bigram Jaccard>=threshold 判为同一事件，
    双向写 cross_source_dup 标注并返回重叠率。新浪是否降级由数日该比率决定（账本 v1.13）。
    """
    group_a = [it for it in items if str(it.get("source_id") or it.get("source")) == source_a]
    group_b = [it for it in items if str(it.get("source_id") or it.get("source")) == source_b]
    grams_b = [(it, _bigrams(it.get("title"))) for it in group_b]
    matched = 0
    pairs = []
    for it in group_a:
        ga = _bigrams(it.get("title"))
        if not ga:
            continue
        best, best_b = 0.0, None
        for it_b, gb in grams_b:
            if not gb:
                continue
            inter = len(ga & gb)
            if not inter:
                continue
            score = inter / len(ga | gb)
            if score > best:
                best, best_b = score, it_b
        if best >= threshold and best_b is not None:
            matched += 1
            it["cross_source_dup"] = {"source": source_b, "title": best_b.get("title"),
                                      "jaccard": round(best, 3)}
            best_b["cross_source_dup"] = {"source": source_a, "title": it.get("title"),
                                          "jaccard": round(best, 3)}
            if len(pairs) < 10:
                pairs.append({"sina": it.get("title"), "cls": best_b.get("title"),
                              "jaccard": round(best, 3)})
    return {"source_a": source_a, "source_b": source_b,
            "a_items": len(group_a), "b_items": len(group_b), "a_matched": matched,
            "a_overlap_rate": round(matched / len(group_a), 3) if group_a else None,
            "threshold": threshold, "pairs_sample": pairs}


def get_news_window(trade_date, cutoff_at=None, *, evidence_dir, start_at):
    """读取归档并产出门禁后新闻窗口。

    参数显式化（evidence_dir / start_at 由调用方给），影子期不与生产状态耦合。
    返回 (items, meta)：items 已按 核验状态→发布时间倒序→来源 排序；
    meta 含四分类计数，供质量门与回执使用。
    """
    cutoff_at = (cutoff_at or datetime.now(CST)).astimezone(CST)
    evidence_dir = Path(evidence_dir)
    rows = {}
    current = start_at.date()
    while current <= cutoff_at.date():
        path = evidence_dir / f"news_{current.isoformat()}.jsonl"
        if path.exists():
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    try:
                        item = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(item, dict) or not item.get("title"):
                        continue
                    collected = _parse_collected(item)
                    if collected is None or collected < start_at or collected > cutoff_at:
                        continue
                    rows.setdefault(_identity(item), item)
        current += timedelta(days=1)

    items = []
    counts = {"fresh_verified": 0, "fresh_unverified": 0, "expired": 0, "time_unknown": 0}
    for item in rows.values():
        status, published, precision = _classify(item, start_at, cutoff_at)
        source = str(item.get("source_id") or item.get("source") or "unknown")
        item["news_window_status"] = status
        item["published_at_parsed"] = published.isoformat() if published else None
        item["published_at_precision"] = precision
        if status == "fresh" and source in VERIFIED_SOURCES and precision == "second":
            item["published_at_status"] = "verified"
            counts["fresh_verified"] += 1
        elif status == "fresh":
            item["published_at_status"] = "unverified" if published else "missing"
            counts["fresh_unverified"] += 1
        elif status == "expired":
            item["published_at_status"] = "expired"
            counts["expired"] += 1
        else:
            item.setdefault("published_at_status", "missing")
            counts["time_unknown"] += 1
        items.append(item)

    # Python sort 稳定：先按发布时间倒序，再按核验状态稳定分组——
    # 结果即 verified 组内发布时间倒序、unverified 其次、missing/expired 殿后（陷阱 3：不凑配额）
    items.sort(key=lambda it: it.get("published_at_parsed") or "", reverse=True)
    items.sort(key=lambda it: {"verified": 0, "unverified": 1}.get(it.get("published_at_status"), 2))

    overflow_dropped = 0
    if len(items) > OVERFLOW_GUARD:
        overflow_dropped = len(items) - OVERFLOW_GUARD
        items = items[:OVERFLOW_GUARD]

    meta = {"trade_date": trade_date, "start_at": start_at.isoformat(), "cutoff_at": cutoff_at.isoformat(),
            "total_after_dedup": len(items) + overflow_dropped, "delivered": len(items), "counts": counts,
            "overflow_guard_triggered": overflow_dropped > 0, "overflow_dropped": overflow_dropped,
            "cross_source_overlap": cross_source_overlap(items),
            "verified_sources": sorted(VERIFIED_SOURCES), "module": "news_window shadow v1"}
    return items, meta
