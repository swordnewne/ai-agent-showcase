#!/usr/bin/env python3
"""
汇率历史积累

为什么需要：
  161130 是 QDII 基金，净值估算公式为
      估算净值 = 官方净值 × (纳指期货最新/纳指昨收) × (汇率最新/汇率昨收)
  其中"汇率昨收"必须有真实来源。

  新浪外汇接口 (fx_susdcny) 的字段布局没有权威文档，实测与常见假设不符
  （第 4 列返回 53.0，明显不是价格字段）。靠猜测字段下标属于"依赖隐含行为"，
  一旦新浪调整布局就会静默算错——而静默算错比报错更危险。

  因此改为自行积累：每个交易日记录一次汇率快照，用真实历史值做对比，
  不依赖任何未文档化的字段位置。

数据文件: data/fx_history.db
"""

import os
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path

WORKSPACE = os.environ.get("NIKO_WORKSPACE") or "/root/.openclaw/workspace"
DB_PATH = Path(WORKSPACE) / "data" / "fx_history.db"
CST = timezone(timedelta(hours=8))


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS fx_snapshots (
            trade_date TEXT PRIMARY KEY,
            usd_cny REAL NOT NULL,
            captured_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    return conn


def record_fx(trade_date: str, usd_cny: float) -> None:
    """记录某交易日的汇率快照（重复运行覆盖当日值）"""
    if not usd_cny or usd_cny <= 0:
        return
    conn = _connect()
    conn.execute(
        "INSERT OR REPLACE INTO fx_snapshots (trade_date, usd_cny) VALUES (?, ?)",
        (trade_date, float(usd_cny)),
    )
    conn.commit()
    conn.close()


def get_fx_prev(before_date: str, max_age_days: int = 10):
    """
    取 before_date 之前最近一次的汇率快照

    Returns:
        (value, date) 或 (None, None)
    """
    if not before_date:
        return (None, None)
    cutoff = (
        datetime.strptime(before_date, "%Y-%m-%d") - timedelta(days=max_age_days)
    ).strftime("%Y-%m-%d")

    conn = _connect()
    row = conn.execute(
        """SELECT trade_date, usd_cny FROM fx_snapshots
           WHERE trade_date < ? AND trade_date >= ?
           ORDER BY trade_date DESC LIMIT 1""",
        (before_date, cutoff),
    ).fetchone()
    conn.close()
    return (row[1], row[0]) if row else (None, None)


def fx_change_ratio(latest: float, prev: float):
    """
    计算汇率变化比率，并做合理性校验

    单日汇率波动超过 ±10% 基本不可能，触发即视为数据异常，
    宁可返回 None 让上游退化，也不要算出一个错得离谱的估值。
    """
    if not latest or not prev or prev <= 0 or latest <= 0:
        return None
    ratio = latest / prev
    if ratio < 0.90 or ratio > 1.10:
        return None
    return ratio


def stats() -> dict:
    conn = _connect()
    rows = conn.execute(
        "SELECT trade_date, usd_cny FROM fx_snapshots ORDER BY trade_date"
    ).fetchall()
    conn.close()
    return {
        "count": len(rows),
        "first": rows[0] if rows else None,
        "last": rows[-1] if rows else None,
    }


if __name__ == "__main__":
    s = stats()
    print(f"汇率快照: {s['count']} 条")
    if s["first"]:
        print(f"  最早: {s['first'][0]} = {s['first'][1]}")
        print(f"  最新: {s['last'][0]} = {s['last'][1]}")
