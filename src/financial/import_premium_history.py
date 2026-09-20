#!/usr/bin/env python3
"""
导入 161130 历史行情与溢价率 → premium history 库

用途：
  preopen_report 的「历史分位点」依赖 premium_pct 序列。
  自动采集每天只产生 1 条，冷启动阶段分位点无意义。
  本脚本把用户提供的历史 CSV 一次性灌入，补上基线。

⚠️ 指标口径差异（重要，不要忽略）：
  本脚本导入的 premium_pct = 当日收盘 / 当日单位净值 − 1（已实现溢价率）
  自动采集写入的 premium_pct = 前收盘价 / 估算净值 − 1（前瞻估算偏离率）

  两者同族但不等价：后者把隔夜期货/汇率变动计入分母。
  当隔夜波动小的时候两者接近，波动大时会分叉。
  报告中引用分位点时必须说明"对比的是历史已实现溢价率分布"。

用法：
  python3 import_premium_history.py <csv路径>
  python3 import_premium_history.py --stats
"""

import csv
import os
import sqlite3
import sys
from pathlib import Path

WORKSPACE = os.environ.get("NIKO_WORKSPACE") or "/root/.openclaw/workspace"
DB_PATH = Path(WORKSPACE) / "data" / "fund_161130_history.db"


def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS fund_161130_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_date TEXT UNIQUE NOT NULL,
            nav REAL,
            price REAL,
            premium_pct REAL,
            nav_date TEXT,
            source TEXT DEFAULT 'auto',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    return conn


def _num(v):
    v = (v or "").strip()
    if not v:
        return None
    try:
        return float(v)
    except ValueError:
        return None


def _pick(row: dict, *names):
    """按候选列名取第一个非空值（不同数据源的列名不一致）"""
    for n in names:
        if n in row and (row[n] or "").strip():
            return row[n]
    return None


def import_csv(csv_path: str) -> dict:
    path = Path(csv_path)
    if not path.exists():
        return {"ok": False, "error": f"文件不存在: {csv_path}"}

    conn = _connect()
    inserted, skipped, updated = 0, 0, []

    # utf-8-sig 处理 BOM（表头首列是 '\ufeff日期'）
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            trade_date = (_pick(row, "日期", "交易日期", "date") or "").strip()
            price = _num(_pick(row, "收盘价", "收盘", "close"))
            nav = _num(_pick(row, "单位净值", "净值", "nav"))
            premium = _num(_pick(row, "溢价率%", "溢价率", "premium"))

            if not trade_date or price is None:
                skipped += 1
                continue

            # 该日期是否已有自动采集记录
            exist = conn.execute(
                "SELECT source, nav, premium_pct FROM fund_161130_history WHERE trade_date=?",
                (trade_date,),
            ).fetchone()

            # 已有回填数据则跳过（幂等）
            if exist and exist[0] == "backfill":
                skipped += 1
                continue

            if exist:
                updated.append({
                    "date": trade_date,
                    "old": {"source": exist[0], "nav": exist[1], "premium_pct": exist[2]},
                    "new": {"nav": nav, "premium_pct": premium},
                })

            conn.execute(
                """INSERT OR REPLACE INTO fund_161130_history
                   (trade_date, nav, price, premium_pct, nav_date, source)
                   VALUES (?, ?, ?, ?, ?, 'backfill')""",
                (trade_date, nav, price, premium, trade_date),
            )
            inserted += 1

    conn.commit()

    total = conn.execute("SELECT COUNT(*) FROM fund_161130_history").fetchone()[0]
    with_pct = conn.execute(
        "SELECT COUNT(*) FROM fund_161130_history WHERE premium_pct IS NOT NULL"
    ).fetchone()[0]
    rng = conn.execute(
        "SELECT MIN(trade_date), MAX(trade_date) FROM fund_161130_history"
    ).fetchone()
    conn.close()

    return {
        "ok": True,
        "inserted": inserted,
        "skipped": skipped,
        "overwritten": updated,
        "total": total,
        "with_premium": with_pct,
        "range": rng,
    }


def stats() -> dict:
    conn = _connect()
    rows = conn.execute(
        """SELECT trade_date, nav, price, premium_pct, source
           FROM fund_161130_history ORDER BY trade_date"""
    ).fetchall()
    conn.close()
    if not rows:
        return {"count": 0}
    pcts = [r[3] for r in rows if r[3] is not None]
    return {
        "count": len(rows),
        "with_premium": len(pcts),
        "min": min(pcts) if pcts else None,
        "max": max(pcts) if pcts else None,
        "avg": round(sum(pcts) / len(pcts), 2) if pcts else None,
        "rows": rows,
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    if sys.argv[1] == "--stats":
        s = stats()
        print(f"记录数: {s['count']}  含溢价率: {s.get('with_premium')}")
        if s.get("count"):
            print(f"溢价率区间: {s['min']}% ~ {s['max']}%  均值: {s['avg']}%")
            print()
            for d, nav, price, pct, src in s["rows"]:
                print(f"  {d}  净值={nav}  收盘={price}  溢价={pct}%  [{src}]")
    else:
        res = import_csv(sys.argv[1])
        if not res["ok"]:
            print(f"❌ {res['error']}")
            sys.exit(1)
        print(f"✅ 导入完成: 写入 {res['inserted']} 条 / 跳过 {res['skipped']} 条")
        if res["overwritten"]:
            print(f"   覆盖已有自动记录 {len(res['overwritten'])} 条:")
            for u in res["overwritten"]:
                print(f"     {u['date']}: {u['old']} → {u['new']}")
        print(f"   库内总计 {res['total']} 条（含溢价率 {res['with_premium']} 条）")
        print(f"   日期范围: {res['range'][0]} ~ {res['range'][1]}")
