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
import sys
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
    columns = {row[1] for row in conn.execute("PRAGMA table_info(preopen_runs)")}
    if "delivery_message_id" not in columns:
        conn.execute("ALTER TABLE preopen_runs ADD COLUMN delivery_message_id TEXT")
        columns.add("delivery_message_id")
        conn.commit()
    if "news_cutoff_at" not in columns:
        conn.execute("ALTER TABLE preopen_runs ADD COLUMN news_cutoff_at TEXT")
        columns.add("news_cutoff_at")
        conn.commit()
    return conn


def today_str() -> str:
    """当前中国日期 YYYY-MM-DD"""
    return datetime.now(CST).strftime("%Y-%m-%d")


def record_run(run_date: str, run_slot: str, status: str, report_path=None, news_cutoff_at=None) -> int:
    """记录一次运行"""
    if status not in VALID_STATUS:
        raise ValueError(f"非法状态: {status}")
    conn = _connect()
    cursor = conn.execute(
        "INSERT INTO preopen_runs (run_date, run_slot, status, report_path, news_cutoff_at) VALUES (?,?,?,?,?)",
        (run_date, run_slot, status, str(report_path) if report_path else None, news_cutoff_at),
    )
    run_id = cursor.lastrowid
    conn.commit()
    conn.close()
    if os.environ.get("NIKO_FINANCE_PG_SHADOW_WRITE") == "1":
        try:
            sys.path.insert(0, os.environ.get("NIKO_FINANCE_ADAPTER_PATH", "/root/.openclaw/workspace/finance-adapter"))
            from preopen_shadow_adapter import write_shadow_run
            write_shadow_run(
                dsn_file=os.environ.get("NIKO_FINANCE_PG_DSN_FILE", "/root/.config/niko/finance-shadow.dsn"),
                run_date=run_date,
                run_slot=run_slot,
                status=status,
                report_path=str(report_path) if report_path else None,
            )
        except Exception as exc:
            # SQLite remains authoritative during the shadow phase; do not
            # turn a PostgreSQL probe failure into a missing report.
            print(f"[告警] PostgreSQL 影子运行记录失败: {type(exc).__name__}: {exc}", file=sys.stderr)
    return run_id


def mark_delivery(run_date: str, delivery_status: str, *, run_id: int = None, message_id: str = None) -> None:
    """Update the exact run; legacy callers may still select the latest date row."""
    if delivery_status not in ("pending", "delivered", "failed"):
        raise ValueError(f"非法投递状态: {delivery_status}")
    conn = _connect()
    predicate = "id=? AND run_date=?" if run_id is not None else "id=(SELECT MAX(id) FROM preopen_runs WHERE run_date=?)"
    params = [run_id, run_date] if run_id is not None else [run_date]
    conn.execute(
        f"""UPDATE preopen_runs SET delivery_status=?,
            delivery_message_id=COALESCE(?, delivery_message_id) WHERE {predicate}""",
        [delivery_status, message_id, *params],
    )
    conn.commit()
    conn.close()


def get_run(run_id: int) -> dict:
    conn = _connect()
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM preopen_runs WHERE id=?", (run_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def completed_run(run_date: str) -> dict:
    conn = _connect()
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM preopen_runs WHERE run_date=? AND status='complete' ORDER BY id DESC",
        (run_date,),
    ).fetchall()
    conn.close()
    return next((dict(row) for row in rows if row['report_path'] and Path(row['report_path']).is_file()), None)


def previous_news_cutoff(before_date: str) -> tuple[str | None, str]:
    """Use the last completed preopen run, excluding evening reports and retries."""
    conn = _connect()
    row = conn.execute(
        """SELECT run_date, run_slot, news_cutoff_at FROM preopen_runs
           WHERE run_date < ? AND status='complete' AND run_slot IN ('08:05', '08:15')
           ORDER BY run_date DESC, id DESC LIMIT 1""", (before_date,),
    ).fetchone()
    conn.close()
    if not row:
        return None, "bootstrap"
    if row[2]:
        return row[2], "completed_preopen_run"
    # Legacy rows did not retain the analysis cutoff. Start at that run's slot.
    return f"{row[0]}T{row[1]}:00+08:00", "legacy_run_slot"


def is_done_today(run_date: str = None):
    """
    严格幂等判定：今天是否有成功的运行

    Returns:
        (bool, report_path_or_None)
    """
    run_date = run_date or today_str()
    row = completed_run(run_date)
    return (True, row['report_path']) if row else (False, None)


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
