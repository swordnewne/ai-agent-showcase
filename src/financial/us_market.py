#!/usr/bin/env python3
"""
纳斯达克100 日线 —— 用于 QDII 净值估算

为什么需要：
  161130 是纳指100 QDII。经 19 个交易日实证对齐，确认映射关系为：

      净值(中国日 T)  ↔  纳指100(美国日 T) 收盘

  吻合度极高（多数交易日误差 < 0.06 个百分点）。

  而美国 T 日的收盘发生在 中国 T+1 凌晨 05:00，因此：
      · 净值(T) 最早中国 T+1 凌晨才有计算原料
      · 按基金合同，T日净值在 T+1 日内计算、T+2 日内披露

  推论：盘前（08:15）拿到的「最新净值」天生滞后约 2 个交易日。

  估算目标日 D 的净值时，从「最新净值基准日」到 D 之间可能隔着多个美国交易日：
      · 已收盘的场次 → 用实际收盘价（确定性数据，不该漏）
      · 尚未收盘的场次 → 只能用期货预期

  早期实现只乘了期货当日涨跌，把已收盘场次的累计变动整个漏掉，
  导致估算净值系统性偏低、偏离率被高估。

⚠️ 数据源陷阱（已踩过）:
  东方财富 secid=100.NDX 名为「纳斯达克」，实测为**纳斯达克综合指数(IXIC)**，
  9/18 收 26522.54；而基金跟踪的纳斯达克100(.NDX) 同日为 29644.17，相差约 10%。
  误用会导致估算错误。本模块统一使用新浪美股日线 symbol='.NDX'。
"""

import json
import re
import time
from pathlib import Path

import requests

SINA_US_K = "https://stock.finance.sina.com.cn/usstock/api/jsonp_v2.php/x/US_MinKService.getDailyK"
NDX_SYMBOL = ".NDX"  # 纳斯达克100（非综合指数）
CACHE_FILE = Path("/tmp/niko_ndx_closes.json")
CACHE_TTL_SEC = 1800  # 30 分钟


def fetch_ndx_closes(use_cache: bool = True) -> dict:
    """
    抓取纳斯达克100 日线收盘价

    Returns:
        {"2026-09-17": 29446.98, "2026-09-18": 29644.17, ...}
        失败返回 {}
    """
    if use_cache and CACHE_FILE.exists():
        try:
            age = time.time() - CACHE_FILE.stat().st_mtime
            if age < CACHE_TTL_SEC:
                data = json.loads(CACHE_FILE.read_text())
                if data:
                    return data
        except Exception:
            pass

    try:
        r = requests.get(
            SINA_US_K,
            params={"symbol": NDX_SYMBOL, "___qn": "n3n"},
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn"},
            timeout=25,
        )
        m = re.search(r"x\((\[.*\])\)", r.text, re.S)
        if not m:
            return {}
        out = {}
        for row in json.loads(m.group(1)):
            d, c = row.get("d"), row.get("c")
            if d and c:
                try:
                    out[d] = float(c)
                except ValueError:
                    continue
        if out:
            try:
                CACHE_FILE.write_text(json.dumps(out))
            except Exception:
                pass
        return out
    except Exception:
        return {}


def cumulative_index_ratio(base_date: str, cutoff_date: str, closes: dict = None) -> dict:
    """
    计算从 base_date 到 cutoff_date 之前「已完成场次」的指数累计比率

    语义：
      · base_date   = 最新已披露净值所对应的纳指日期（中国日 T ↔ 美国日 T）
      · cutoff_date = 目标交易日 D（报告日）。只取 < D 的收盘，
                      因为美国 D 日尚未收盘，不能算作已完成场次。

    Returns:
      {
        "ok": True, "ratio": 1.00395,
        "from_date": "2026-09-17", "from_close": 29446.98,
        "to_date": "2026-09-18",   "to_close": 29644.17,
        "sessions": ["2026-09-18"],
        "stale_sessions": 1
      }
      数据不足时 ok=False
    """
    closes = closes if closes is not None else fetch_ndx_closes()
    if not closes or not base_date or not cutoff_date:
        return {"ok": False, "ratio": 1.0, "reason": "缺少指数数据"}

    if base_date not in closes:
        return {"ok": False, "ratio": 1.0, "reason": f"基准日 {base_date} 无指数收盘"}

    done = sorted(d for d in closes if base_date < d < cutoff_date)
    if not done:
        return {
            "ok": True, "ratio": 1.0,
            "from_date": base_date, "from_close": closes[base_date],
            "to_date": base_date, "to_close": closes[base_date],
            "sessions": [], "stale_sessions": 0,
        }

    latest = done[-1]
    return {
        "ok": True,
        "ratio": closes[latest] / closes[base_date],
        "from_date": base_date,
        "from_close": closes[base_date],
        "to_date": latest,
        "to_close": closes[latest],
        "sessions": done,
        "stale_sessions": len(done),
    }


if __name__ == "__main__":
    closes = fetch_ndx_closes()
    print(f"纳斯达克100 日线: {len(closes)} 条")
    if closes:
        ds = sorted(closes)
        print(f"  范围: {ds[0]} ~ {ds[-1]}")
        for d in ds[-6:]:
            print(f"    {d}  {closes[d]}")

    print()
    print("--- 模拟 2026-09-21 盘前估算 ---")
    r = cumulative_index_ratio("2026-09-17", "2026-09-21", closes)
    if r["ok"]:
        print(f"  基准: {r['from_date']} = {r['from_close']}")
        print(f"  截至: {r['to_date']} = {r['to_close']}")
        print(f"  已完成场次: {r['sessions']}")
        print(f"  累计比率: {r['ratio']:.6f}  ({(r['ratio']-1)*100:+.3f}%)")
        nav = 4.4266
        print(f"  修正后估算净值: {nav} × {r['ratio']:.6f} = {nav*r['ratio']:.4f}")
        print(f"  （旧公式漏算这一段，会得 {nav:.4f}）")
    else:
        print(f"  失败: {r.get('reason')}")
