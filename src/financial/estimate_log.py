#!/usr/bin/env python3
"""
估算值落库与事后对账

为什么需要：
  preopen_report 每天产出一个「估算净值」，但估算准不准从来没有被度量过。
  此前只能拿 16 个历史交易日做一次性回测（结果显示平均误差 0.75~0.92pp）。

  只有持续记录「当时估了多少」+「事后真实是多少」，才能得到真实的误差分布，
  从而判断这个估算值到底该被赋予多少信任。

  设计要点:
    · 每次运行都写入一条估算记录（原文保留，不做事后修饰）
    · 净值正式披露后回填 actual_nav 并计算误差
    · 对账数据源用基金公司正式净值，不用自己抓的（避免滞后污染）

数据文件: data/nav_estimates.db
"""

import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

WORKSPACE = os.environ.get("NIKO_WORKSPACE") or "/root/.openclaw/workspace"
DB_PATH = Path(WORKSPACE) / "data" / "nav_estimates.db"
CST = timezone(timedelta(hours=8))

LSJZ_URL = "https://api.fund.eastmoney.com/f10/lsjz"


def _connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS nav_estimates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            target_date TEXT NOT NULL,
            estimated_at TEXT NOT NULL,
            slot TEXT,
            base_nav REAL,
            base_nav_date TEXT,
            index_ratio REAL,
            index_sessions TEXT,
            futures_change REAL,
            fx_change REAL,
            estimated_nav REAL,
            price REAL,
            price_date TEXT,
            broker_premium_pct REAL,
            est_deviation_pct REAL,
            actual_nav REAL,
            error_pp REAL,
            reconciled_at TEXT,
            UNIQUE(target_date, slot)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_est_target ON nav_estimates(target_date)")
    conn.commit()
    return conn


def log_estimate(rec: dict) -> bool:
    """
    记录一次估算

    rec 需含 target_date；其余字段可缺。同 (target_date, slot) 唯一，重复运行覆盖。
    """
    if not rec.get("target_date"):
        return False
    na = rec.get("estimated_nav")
    pr = rec.get("price")
    dev = None
    if na and pr:
        try:
            dev = (pr / na - 1) * 100
        except ZeroDivisionError:
            dev = None

    sessions = rec.get("index_sessions")
    if isinstance(sessions, (list, tuple)):
        sessions = ",".join(sessions)

    conn = _connect()
    conn.execute("""
        INSERT OR REPLACE INTO nav_estimates
        (target_date, estimated_at, slot, base_nav, base_nav_date, index_ratio,
         index_sessions, futures_change, fx_change, estimated_nav, price, price_date,
         broker_premium_pct, est_deviation_pct, actual_nav, error_pp, reconciled_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL,NULL,NULL)
    """, (
        rec.get("target_date"),
        rec.get("estimated_at") or datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S"),
        rec.get("slot"),
        rec.get("base_nav"),
        rec.get("base_nav_date"),
        rec.get("index_ratio"),
        sessions,
        rec.get("futures_change"),
        rec.get("fx_change"),
        na,
        pr,
        rec.get("price_date"),
        rec.get("broker_premium_pct"),
        dev,
    ))
    conn.commit()
    conn.close()
    return True


def fetch_official_navs(page_size: int = 60, max_pages: int = 6) -> dict:
    """
    抓取基金公司正式披露的净值（东财，authoritative）

    Returns:
        {"2026-09-17": 4.4266, ...}
    """
    out = {}
    for page in range(1, max_pages + 1):
        try:
            r = requests.get(
                LSJZ_URL,
                params={"fundCode": "161130", "pageIndex": page, "pageSize": page_size},
                headers={"Referer": "https://fundf10.eastmoney.com/", "User-Agent": "Mozilla/5.0"},
                timeout=20,
            )
            items = (r.json().get("Data") or {}).get("LSJZList") or []
            if not items:
                break
            for it in items:
                d, v = it.get("FSRQ"), it.get("DWJZ")
                if d and v:
                    try:
                        out[d] = float(v)
                    except ValueError:
                        continue
        except Exception:
            break
    return out


def reconcile() -> dict:
    """
    回填真实净值并计算误差（仅处理已披露的日期）
    """
    official = fetch_official_navs()
    if not official:
        return {"ok": False, "reason": "无法获取正式净值"}

    conn = _connect()
    pending = conn.execute(
        "SELECT id, target_date, estimated_nav FROM nav_estimates WHERE actual_nav IS NULL"
    ).fetchall()

    filled = 0
    for row_id, tdate, est in pending:
        actual = official.get(tdate)
        if actual is None:
            continue  # 净值尚未披露，留待下次
        err = None
        if est:
            err = (est / actual - 1) * 100
        conn.execute(
            """UPDATE nav_estimates
               SET actual_nav=?, error_pp=?, reconciled_at=?
               WHERE id=?""",
            (actual, err, datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S"), row_id),
        )
        filled += 1

    conn.commit()
    conn.close()
    return {"ok": True, "filled": filled, "pending": len(pending) - filled}


def error_stats(days: int = 120) -> dict:
    """估算误差统计（仅统计已对账的记录）"""
    cutoff = (datetime.now(CST) - timedelta(days=days)).strftime("%Y-%m-%d")
    conn = _connect()
    rows = conn.execute(
        """SELECT target_date, base_nav_date, estimated_nav, actual_nav, error_pp
           FROM nav_estimates
           WHERE error_pp IS NOT NULL AND target_date >= ?
           ORDER BY target_date""",
        (cutoff,),
    ).fetchall()
    conn.close()

    if not rows:
        return {"count": 0}

    errs = [abs(r[4]) for r in rows]
    return {
        "count": len(rows),
        "mean_abs_error_pp": round(sum(errs) / len(errs), 3),
        "max_abs_error_pp": round(max(errs), 3),
        "min_abs_error_pp": round(min(errs), 3),
        "first": rows[0][0],
        "last": rows[-1][0],
        "rows": rows,
    }


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "--reconcile":
        print(reconcile())
    elif len(sys.argv) > 1 and sys.argv[1] == "--stats":
        s = error_stats()
        if not s.get("count"):
            print("暂无已对账记录")
        else:
            print(f"已对账 {s['count']} 条  ({s['first']} ~ {s['last']})")
            print(f"  平均绝对误差: {s['mean_abs_error_pp']} pp")
            print(f"  最大: {s['max_abs_error_pp']} pp   最小: {s['min_abs_error_pp']} pp")
            print()
            for d, bd, est, act, e in s["rows"]:
                print(f"  {d}  基准{bd}  估算{est}  实际{act}  误差{e:+.3f}pp")
    else:
        print(__doc__)
