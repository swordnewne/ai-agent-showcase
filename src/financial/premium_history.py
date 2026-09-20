#!/usr/bin/env python3
"""
161130 历史溢价数据积累模块
从今天开始每天自动记录：日期、净值、价格、溢价率
"""

import os
import sqlite3
import statistics
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


def multi_window_percentile(current_premium: float, windows=(30, 60, 120, 250, None)) -> dict:
    """
    多窗口分位点

    为什么必须多窗口:
      两年数据显示 161130 的溢价率中枢明显移动，但**并非单调上升** ——
      60 日滚动均值在 -0.8% ~ +4.0% 之间周期性摆动:
        2025-07  -0.77%（折价）
        2026-01  +2.73%
        2026-05  +0.40%
        2026-09  +3.96%（样本内最高）
      中枢移动 + 周期摆动，意味着分布非平稳。

      同一溢价率在不同窗口下分位差异极大（实测 4.69%: 同制度 79% / 近30日 73% / 全样本 92%）。
      单一窗口会误导，因此并列输出，并附平稳性提示。

    以【记录条数】而非日历天数取窗口 —— 交易日更均匀。
    """
    init_db()
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT trade_date, premium_pct FROM fund_161130_history "
        "WHERE premium_pct IS NOT NULL ORDER BY trade_date"
    ).fetchall()
    conn.close()

    series = [p for _, p in rows]
    if not series:
        return {"current": current_premium, "windows": [], "has_data": False}

    out = []

    # 同制度窗口 —— 申购制度是溢价的强解释变量（暂停期均值比开放期高 2.09pp），
    # 用开放期的分布衡量暂停期的溢价会系统性低估，故优先给同制度分位。
    try:
        from subscription_regimes import regime_at, regime_span
        target = datetime.now().strftime("%Y-%m-%d")
        reg = regime_at(target)
        span = regime_span(target)

        def _win(days, label, extra=None):
            if len(days) < 10:
                return None
            below = sum(1 for x in days if x < current_premium) / len(days) * 100
            d = {
                "label": label, "n": len(days),
                "median": round(statistics.median(days), 2),
                "mean": round(statistics.mean(days), 2),
                "below_pct": round(below, 1),
            }
            if extra:
                d["regime"] = extra
            return d

        # 同档：当前这一档具体制度的区间（精确，但样本较短）
        same = [p for d, p in rows
                if span[0] and d >= span[0] and (span[1] is None or d <= span[1])]
        e = _win(same, f"同档（自 {reg['start']}）", reg)
        if e:
            out.append(e)

        # 同类：同一开放度类别（实质关闭 / 实质开放）的全部交易日（样本更厚）
        kind = reg["kind"]
        if kind:
            same_kind = [p for d, p in rows if (regime_at(d) or {}).get("kind") == kind]
            e2 = _win(same_kind, f"同类（{reg['label']}）", {"kind": kind})
            if e2:
                out.append(e2)
    except Exception:
        pass

    for w in windows:
        sub = series[-w:] if w and w < len(series) else series
        if len(sub) < 5:
            continue
        below = sum(1 for x in sub if x < current_premium) / len(sub) * 100
        out.append({
            "label": f"近{w}日" if (w and w < len(series)) else "全样本",
            "n": len(sub),
            "median": round(statistics.median(sub), 2),
            "below_pct": round(below, 1),
            "mean": round(statistics.mean(sub), 2),
        })

    # 平稳性检测：前后两个等长窗口的均值差异
    regime_note = ""
    if len(series) >= 60:
        half = len(series) // 2
        early, late = series[:half], series[half:]
        md = statistics.mean(late) - statistics.mean(early)
        if abs(md) >= 0.8:
            regime_note = (
                f"⚠️ 分布非平稳：后半段均值 {statistics.mean(late):.2f}% vs "
                f"前半段 {statistics.mean(early):.2f}%（差 {md:+.2f}pp）。"
                "历史分位仅供参考——溢价中枢本身在移动，"
                "分位点所假设的平稳分布在此不成立。"
            )

    return {
        "current": current_premium,
        "windows": out,
        "has_data": True,
        "regime_note": regime_note,
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
