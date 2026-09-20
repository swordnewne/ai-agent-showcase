#!/usr/bin/env python3
"""
161130 历史溢价数据积累模块
从今天开始每天自动记录：日期、净值、价格、溢价率
"""

import sqlite3
import os
from datetime import datetime, timedelta
from pathlib import Path

DB_DIR = Path(os.environ.get("NIKO_WORKSPACE", "/root/.openclaw/workspace")) / "data"
DB_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DB_DIR / "fund_161130_history.db"


def init_db():
    """初始化数据库"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
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
    conn.close()


def save_record(trade_date: str, nav: float, price: float, premium_pct: float, nav_date: str = None):
    """保存每日记录"""
    init_db()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT OR REPLACE INTO fund_161130_history 
        (trade_date, nav, price, premium_pct, nav_date, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (trade_date, nav, price, premium_pct, nav_date, datetime.now().isoformat()))
    conn.commit()
    conn.close()


def get_recent_stats(days: int = 30) -> dict:
    """获取近N天统计"""
    init_db()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    cursor.execute("""
        SELECT trade_date, nav, price, premium_pct
        FROM fund_161130_history
        WHERE trade_date >= ?
        ORDER BY trade_date DESC
    """, (cutoff,))
    
    rows = cursor.fetchall()
    conn.close()
    
    if not rows:
        return {"count": 0, "message": "暂无历史数据"}
    
    premiums = [r[3] for r in rows if r[3] is not None]
    return {
        "count": len(rows),
        "days": days,
        "avg_premium": round(sum(premiums) / len(premiums), 2) if premiums else None,
        "min_premium": round(min(premiums), 2) if premiums else None,
        "max_premium": round(max(premiums), 2) if premiums else None,
        "latest": rows[0],
    }


def get_percentile(current_premium: float, days: int = 30) -> dict:
    """计算当前溢价率的分位点"""
    init_db()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    cursor.execute("""
        SELECT premium_pct FROM fund_161130_history
        WHERE trade_date >= ? AND premium_pct IS NOT NULL
        ORDER BY premium_pct
    """, (cutoff,))
    
    rows = cursor.fetchall()
    conn.close()
    
    if not rows:
        return {"has_data": False}
    
    premiums = [r[0] for r in rows]
    count = len(premiums)
    
    # 计算分位点
    below = sum(1 for p in premiums if p < current_premium)
    percentile = round(below / count * 100, 1) if count > 0 else 50
    
    return {
        "has_data": True,
        "count": count,
        "percentile": percentile,
        "median": premiums[count // 2] if count > 0 else None,
        "description": f"高于{percentile}%的时间" if percentile > 50 else f"低于{100-percentile}%的时间",
    }


if __name__ == "__main__":
    init_db()
    print(f"数据库初始化完成: {DB_PATH}")
    stats = get_recent_stats(30)
    print(f"近30天记录数: {stats.get('count', 0)}")
