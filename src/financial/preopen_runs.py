#!/usr/bin/env python3
"""
盘前简报运行记录（严格幂等判定依据）

设计要点：
- 只有 status='complete' 才算"今天已执行过"
- partial / failed 允许兜底任务重试
- 生成状态与投递状态分开记录（对齐 LOCAL-OPS 要求）
"""

import sqlite3
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

WORKSPACE = os.environ.get("NIKO_WORKSPACE") or "/root/.openclaw/workspace"
DB_PATH = Path(WORKSPACE) / "data" / "preopen_runs.db"
CST = timezone(timedelta(hours=8))

VALID_STATUS = ("complete", "partial", "failed")


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS preopen_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_date TEXT NOT NULL,
            run_slot TEXT NOT NULL,
            status TEXT NOT NULL,
            report_path TEXT,
            delivery_status TEXT DEFAULT 'pending',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_preopen_run_date ON preopen_runs(run_date)"
    )
    return conn


def today_str() -> str:
    """当前中国日期 YYYY-MM-DD"""
    return datetime.now(CST).strftime("%Y-%m-%d")


def record_run(run_date: str, run_slot: str, status: str, report_path=None) -> None:
    """记录一次运行"""
    if status not in VALID_STATUS:
        raise ValueError(f"非法状态: {status}")
    conn = _connect()
    conn.execute(
        "INSERT INTO preopen_runs (run_date, run_slot, status, report_path) VALUES (?,?,?,?)",
        (run_date, run_slot, status, str(report_path) if report_path else None),
    )
    conn.commit()
    conn.close()


def mark_delivery(run_date: str, delivery_status: str) -> None:
    """更新当日最后一次运行的投递状态"""
    conn = _connect()
    conn.execute(
        """UPDATE preopen_runs SET delivery_status=?
           WHERE id=(SELECT MAX(id) FROM preopen_runs WHERE run_date=?)""",
        (delivery_status, run_date),
    )
    conn.commit()
    conn.close()


def is_done_today(run_date: str = None):
    """
    严格幂等判定：今天是否有成功的运行

    Returns:
        (bool, report_path_or_None)
    """
    run_date = run_date or today_str()
    conn = _connect()
    row = conn.execute(
        """SELECT report_path FROM preopen_runs
           WHERE run_date=? AND status='complete'
           ORDER BY id DESC LIMIT 1""",
        (run_date,),
    ).fetchone()
    conn.close()
    return (True, row[0]) if row else (False, None)


def get_today_runs(run_date: str = None) -> list:
    """获取指定日期的所有运行记录"""
    run_date = run_date or today_str()
    conn = _connect()
    rows = conn.execute(
        """SELECT run_slot, status, report_path, delivery_status, created_at
           FROM preopen_runs WHERE run_date=? ORDER BY id""",
        (run_date,),
    ).fetchall()
    conn.close()
    return rows


if __name__ == "__main__":
    d = today_str()
    done, path = is_done_today(d)
    print(f"日期: {d}")
    print(f"已完成(complete): {'是' if done else '否'}")
    print(f"报告路径: {path or '(无)'}")
    runs = get_today_runs(d)
    if runs:
        print("运行记录:")
        for slot, status, rpath, dstatus, created in runs:
            print(f"  [{slot}] {status} / 投递={dstatus} @ {created}")
