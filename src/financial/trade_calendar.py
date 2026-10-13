#!/usr/bin/env python3
"""Versioned A-share trading calendar stored as a local JSON snapshot."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

WORKSPACE = os.environ.get("NIKO_WORKSPACE") or "/root/.openclaw/workspace"
CACHE_PATH = Path(WORKSPACE) / "data" / "trade_calendar.json"
CST = timezone(timedelta(hours=8))
SSE_URL = "https://www.sse.com.cn/disclosure/dealinstruc/closed"
SCHEMA_VERSION = "finance-trading-calendar-v1"


class CalendarCoverageError(ValueError):
    """Raised when the local calendar has no authoritative state for a date."""


def _load_cache(path: Path | None = None) -> dict:
    path = path or CACHE_PATH
    if not path.exists():
        raise CalendarCoverageError(f"交易日历文件不存在: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CalendarCoverageError(f"交易日历文件不可读: {path}: {exc}") from exc
    if not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION:
        raise CalendarCoverageError("交易日历 schema 缺失或版本不支持")
    return value


def _validated_year(cache: dict, year: int) -> dict[str, bool]:
    years = cache.get("years")
    item = years.get(str(year)) if isinstance(years, dict) else None
    if not isinstance(item, dict) or not isinstance(item.get("days"), list):
        raise CalendarCoverageError(f"交易日历未覆盖 {year} 年，请更新 data/trade_calendar.json")

    states: dict[str, bool] = {}
    expected = date(year, 1, 1)
    end = date(year, 12, 31)
    for row in item["days"]:
        if not isinstance(row, dict):
            raise CalendarCoverageError(f"{year} 年交易日历记录格式无效")
        day = row.get("calendar_date")
        state = row.get("is_trading_day")
        if not isinstance(day, str) or type(state) is not bool:
            raise CalendarCoverageError(f"{year} 年交易日历字段无效: {row!r}")
        try:
            parsed = date.fromisoformat(day)
        except ValueError as exc:
            raise CalendarCoverageError(f"交易日历日期格式无效: {day}") from exc
        if parsed.year != year or parsed.isoformat() != day or day in states:
            raise CalendarCoverageError(f"交易日历日期重复或年份不匹配: {day}")
        states[day] = state

    expected_days = (end - expected).days + 1
    if len(states) != expected_days:
        raise CalendarCoverageError(f"{year} 年交易日历不完整: {len(states)}/{expected_days} 天")
    return states


def is_trading_day(d=None) -> bool:
    """Return the authoritative local-calendar state for a date."""
    if d is None:
        d = datetime.now(CST).date()
    elif isinstance(d, str):
        d = date.fromisoformat(d)
    elif isinstance(d, datetime):
        d = d.date()
    if not isinstance(d, date):
        raise TypeError("date must be a date, datetime, ISO string, or None")
    cache = _load_cache()
    states = _validated_year(cache, d.year)
    day = d.isoformat()
    if day not in states:
        raise CalendarCoverageError(f"交易日历未覆盖日期: {day}")
    return states[day]


def get_trade_date():
    """Return today's ISO date when the local calendar marks it as trading."""
    today = datetime.now(CST).date()
    return today.isoformat() if is_trading_day(today) else None


def reason(d=None) -> str:
    if d is None:
        d = datetime.now(CST).date()
    elif isinstance(d, str):
        d = date.fromisoformat(d)
    elif isinstance(d, datetime):
        d = d.date()
    return f"{d.isoformat()} {'交易日' if is_trading_day(d) else '休市日'}（JSON 官方日历）"


def _parse_sse_holidays(html: str) -> dict[str, list[str]]:
    text = re.sub(r"<[^>]+>", " ", html)
    text = text.replace("&nbsp;", " ").replace("　", " ")
    text = re.sub(r"\s+", " ", text)
    pattern = re.compile(
        r"(\d{1,2})月(\d{1,2})日\s*[（(][^）)]{0,12}[）)]\s*至\s*(\d{1,2})月(\d{1,2})日"
    )
    result: dict[str, list[str]] = {}
    sections = re.split(r"(\d{4})年休市安排", text)
    for i in range(1, len(sections) - 1, 2):
        year = sections[i]
        dates: set[str] = set()
        for match in pattern.finditer(sections[i + 1]):
            m1, d1, m2, d2 = (int(value) for value in match.groups())
            try:
                start = date(int(year), m1, d1)
                end = date(int(year), m2, d2)
            except ValueError:
                continue
            if end < start or (end - start).days > 30:
                continue
            current = start
            while current <= end:
                dates.add(current.isoformat())
                current += timedelta(days=1)
        if dates:
            result[year] = sorted(dates)
    return result


def build_year(year: int, holidays: list[str], *, source: str, source_url: str,
               source_response_hash: str, fetched_at: str) -> dict:
    holiday_set = set(holidays)
    days = []
    current = date(year, 1, 1)
    end = date(year, 12, 31)
    while current <= end:
        trading = current.weekday() < 5 and current.isoformat() not in holiday_set
        days.append({"calendar_date": current.isoformat(), "is_trading_day": trading})
        current += timedelta(days=1)
    return {
        "year": year,
        "calendar_version": f"{year}-v1",
        "source": source,
        "source_url": source_url,
        "source_response_hash": source_response_hash,
        "fetched_at": fetched_at,
        "calendar_basis": "weekdays excluding dates in the SSE-published annual closure schedule",
        "days": days,
    }


def refresh_from_sse(year: int | None = None, timeout: int = 15, path: Path | None = None) -> dict:
    """Fetch the SSE annual closure notice and atomically refresh one full year."""
    path = path or CACHE_PATH
    response = requests.get(
        SSE_URL,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
        timeout=timeout,
    )
    response.raise_for_status()
    response.encoding = response.apparent_encoding or "utf-8"
    parsed = _parse_sse_holidays(response.text)
    target_year = str(year or datetime.now(CST).year)
    holidays = parsed.get(target_year)
    if not holidays:
        raise CalendarCoverageError(f"上交所页面未找到 {target_year} 年休市安排；保留现有日历不变")
    if len(holidays) > 60:
        raise CalendarCoverageError(f"{target_year} 年解析出异常休市天数 {len(holidays)}，拒绝更新")

    fetched_at = datetime.now(CST).isoformat()
    entry = build_year(
        int(target_year), holidays,
        source="sse.com.cn",
        source_url=response.url,
        source_response_hash="sha256:" + hashlib.sha256(response.content).hexdigest(),
        fetched_at=fetched_at,
    )
    cache = _load_cache(path) if path.exists() else {
        "schema_version": SCHEMA_VERSION,
        "calendar_id": "cn-a-share-trading-calendar",
        "years": {},
    }
    cache.setdefault("years", {})[target_year] = entry
    cache["updated_at"] = fetched_at
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(cache, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return {
        "ok": True,
        "year": target_year,
        "calendar_version": entry["calendar_version"],
        "day_count": len(entry["days"]),
        "trading_day_count": sum(row["is_trading_day"] for row in entry["days"]),
        "source_response_hash": entry["source_response_hash"],
        "path": str(path),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="A 股官方交易日历")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--refresh", action="store_true", help="手动从上交所同步指定年度安排")
    group.add_argument("--check", metavar="YYYY-MM-DD", help="查询指定日期交易状态")
    parser.add_argument("--year", type=int, help="刷新或查询的年份")
    args = parser.parse_args()
    if args.refresh:
        print(json.dumps(refresh_from_sse(args.year), ensure_ascii=False, indent=2))
    elif args.check:
        print(reason(args.check))
    else:
        today = datetime.now(CST).date()
        print(reason(today))
