#!/usr/bin/env python3
"""
A股交易日历

设计原则：保守优先 —— 宁可多跑一次，不可漏跑交易日。

判定来源（三层，优先级从高到低）：
  1. 本地缓存 data/trade_calendar.json（由 refresh_from_sse() 在线刷新写入）
  2. 内置官方休市表 BUILTIN_HOLIDAYS（随代码发布，来源：上交所公告）
  3. 兜底：周一至周五视为交易日

注意：A股不跟随调休上班。调休出来的周末（如 10/10 周六）A股休市，
      上交所的休市安排已包含该影响，因此「周一至周五 减 休市日」即为交易日。

刷新：
  python3 trade_calendar.py --refresh      # 从上交所官网抓取并更新缓存
  python3 trade_calendar.py --check 2026-09-25   # 查某天是否交易日
"""

import json
import re
import os
from datetime import datetime, date, timezone, timedelta
from pathlib import Path

WORKSPACE = os.environ.get("NIKO_WORKSPACE") or "/root/.openclaw/workspace"
CACHE_PATH = Path(WORKSPACE) / "data" / "trade_calendar.json"
CST = timezone(timedelta(hours=8))

SSE_URL = "https://www.sse.com.cn/disclosure/dealinstruc/closed"

# 上交所官方休市安排（2026 年）
# 来源: https://www.sse.com.cn/disclosure/dealinstruc/closed
BUILTIN_HOLIDAYS = {
    "2026": [
        # 元旦: 1月1日(四)至1月3日(六)
        "2026-01-01", "2026-01-02", "2026-01-03",
        # 春节: 2月15日(日)至2月23日(一)
        "2026-02-15", "2026-02-16", "2026-02-17", "2026-02-18", "2026-02-19",
        "2026-02-20", "2026-02-21", "2026-02-22", "2026-02-23",
        # 清明节: 4月4日(六)至4月6日(一)
        "2026-04-04", "2026-04-05", "2026-04-06",
        # 劳动节: 5月1日(五)至5月5日(二)
        "2026-05-01", "2026-05-02", "2026-05-03", "2026-05-04", "2026-05-05",
        # 端午节: 6月19日(五)至6月21日(日)
        "2026-06-19", "2026-06-20", "2026-06-21",
        # 中秋节: 9月25日(五)至9月27日(日)
        "2026-09-25", "2026-09-26", "2026-09-27",
        # 国庆节: 10月1日(四)至10月7日(三)
        "2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04", "2026-10-05",
        "2026-10-06", "2026-10-07",
    ]
}


def _load_cache() -> dict:
    if CACHE_PATH.exists():
        try:
            return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _all_holidays() -> set:
    """合并内置表与在线缓存"""
    holidays = set()
    for dates in BUILTIN_HOLIDAYS.values():
        holidays.update(dates)
    for dates in _load_cache().get("holidays", {}).values():
        holidays.update(dates)
    return holidays


def is_trading_day(d=None) -> bool:
    """判断是否为 A股交易日"""
    if d is None:
        d = datetime.now(CST).date()
    elif isinstance(d, str):
        d = datetime.strptime(d, "%Y-%m-%d").date()

    if d.weekday() >= 5:
        return False
    return d.strftime("%Y-%m-%d") not in _all_holidays()


def get_trade_date():
    """今日是交易日则返回 YYYY-MM-DD，否则返回 None"""
    today = datetime.now(CST).date()
    return today.strftime("%Y-%m-%d") if is_trading_day(today) else None


def reason(d=None) -> str:
    """返回判定理由（便于报告/排障）"""
    if d is None:
        d = datetime.now(CST).date()
    elif isinstance(d, str):
        d = datetime.strptime(d, "%Y-%m-%d").date()
    ds = d.strftime("%Y-%m-%d")
    if d.weekday() >= 5:
        return f"{ds} 周末休市"
    if ds in _all_holidays():
        return f"{ds} 法定节假日休市"
    return f"{ds} 交易日"


def _parse_sse_holidays(html: str) -> dict:
    """
    解析上交所休市安排页面

    返回: {"2026": ["2026-01-01", ...], ...}

    注意: 正则必须严格锚定官方格式「X月Y日（星期Z）至X月Y日」。
    早期版本用宽松正则 [^至]{0,30}? 跨句配对，会凭空造出
    2/28-4/30 这种长达两个月的假休市区间，必须避免。
    """
    text = re.sub(r"<[^>]+>", " ", html)
    text = text.replace("&nbsp;", " ").replace("　", " ")
    text = re.sub(r"\s+", " ", text)

    # 严格格式: 1月1日（星期四）至1月3日（星期六）
    pattern = re.compile(
        r"(\d{1,2})月(\d{1,2})日\s*[（(][^）)]{0,12}[）)]\s*至\s*(\d{1,2})月(\d{1,2})日"
    )

    result = {}
    sections = re.split(r"(\d{4})年休市安排", text)
    for i in range(1, len(sections) - 1, 2):
        year = sections[i]
        body = sections[i + 1]
        dates = set()
        for m in pattern.finditer(body):
            m1, d1, m2, d2 = (int(x) for x in m.groups())
            try:
                start = date(int(year), m1, d1)
                end = date(int(year), m2, d2)
            except ValueError:
                continue
            # 单个假期区间不会超过 30 天
            if end < start or (end - start).days > 30:
                continue
            cur = start
            while cur <= end:
                dates.add(cur.strftime("%Y-%m-%d"))
                cur += timedelta(days=1)
        if dates:
            result[year] = sorted(dates)
    return result


def refresh_from_sse(timeout: int = 15) -> dict:
    """从上交所官网抓取休市安排并更新本地缓存"""
    import requests

    try:
        r = requests.get(
            SSE_URL,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
            timeout=timeout,
        )
        r.raise_for_status()
        r.encoding = r.apparent_encoding or "utf-8"
        parsed = _parse_sse_holidays(r.text)
        if not parsed:
            return {"ok": False, "error": "页面未解析出休市日期"}

        # 合理性校验：单年休市日不应超过 60 天（约 33 天为正常）
        for year, dates in parsed.items():
            if len(dates) > 60:
                return {
                    "ok": False,
                    "error": f"{year} 年解析出 {len(dates)} 天，超出合理范围，拒绝写入缓存",
                }

        cache = _load_cache()
        cache.setdefault("holidays", {})
        for year, dates in parsed.items():
            cache["holidays"][year] = dates
        cache["source"] = "sse.com.cn"
        cache["refreshed_at"] = datetime.now(CST).isoformat()
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(
            json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return {
            "ok": True,
            "years": sorted(parsed.keys()),
            "count": sum(len(v) for v in parsed.values()),
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


def ensure_fresh(max_age_days: int = 7) -> dict:
    """
    缓存过期则自动刷新（从上交所官网）

    失败不抛异常：拿不到在线数据时静默回退到内置表，
    绝不因此阻断报告生成。
    """
    cache = _load_cache()
    ts = cache.get("refreshed_at")
    if ts:
        try:
            age = datetime.now(CST) - datetime.fromisoformat(ts)
            if age.days < max_age_days:
                return {"refreshed": False, "reason": f"缓存 {age.days} 天前刷新，仍新鲜"}
        except Exception:
            pass

    res = refresh_from_sse()
    return {"refreshed": res.get("ok", False), **res}


if __name__ == "__main__":
    import sys

    args = sys.argv[1:]
    if "--refresh" in args:
        res = refresh_from_sse()
        print(f"刷新结果: {res}")
    elif "--ensure-fresh" in args:
        print(f"保鲜结果: {ensure_fresh()}")
    elif "--check" in args:
        idx = args.index("--check")
        target = args[idx + 1] if len(args) > idx + 1 else None
        print(f"{reason(target)} → 交易日={is_trading_day(target)}")
    else:
        print(f"今日: {reason()}")
        print(f"是否交易日: {is_trading_day()}")
        cache = _load_cache()
        if cache.get("refreshed_at"):
            print(f"在线缓存刷新于: {cache['refreshed_at']}")
        else:
            print("在线缓存: 未刷新（仅用内置表）")
