#!/usr/bin/env python3
"""
申购政策变更监控

为什么需要：
  实证显示申购制度是溢价的强解释变量:
      暂停申购       均值 +2.73%  (n=164)
      申购开放+限额  均值 +0.64%  (n=301)
  差异 +2.09pp。

  **恢复申购公告是前瞻性信号** —— 意味着套利通道重开，溢价大概率向下。
  这比看历史分位更有决策价值，因为它是「将要发生」而不是「已经发生」。

机制：
  每次运行拉取本基金最新公告，与上次已见集合做差，识别新增的申购政策公告。
  状态存本地，避免重复告警。

数据源: 东方财富基金公告接口（已验证可用）
局限:   公告只给标题与日期，不给限额数值；且无法区分因果方向。
"""

import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

WORKSPACE = os.environ.get("NIKO_WORKSPACE") or "/root/.openclaw/workspace"
STATE_FILE = Path(WORKSPACE) / "data" / "subscription_watch_state.json"
CST = timezone(timedelta(hours=8))

JJGG_URL = "https://api.fund.eastmoney.com/f10/JJGG"
FUND_CODE = "161130"

# 只关心政策变更，排除例行公告
POLICY_KW = re.compile(r"暂停申购|恢复申购|暂停大额申购|恢复大额申购|调整大额申购|申购及定期定额")
# 例行休市型：「YYYY年M月D日暂停申购、赎回...」—— 境外市场节假日
ROUTINE_KW = re.compile(r"\d{4}年\d{1,2}月\d{1,2}日暂停")
# 与其它基金/银行渠道相关的噪声
NOISE_KW = re.compile(r"银行卡|网上直销|旗下部分开放式基金|货币市场基金")


def fetch_policy_announcements(max_pages: int = 3) -> list:
    """
    拉取本基金的申购政策类公告

    Returns:
        [{"id":..., "date":"2026-03-19", "title":"...", "kind":"suspend"|"resume"|"limit_adjust"}, ...]
    """
    seen = {}
    for typ in ("1", "2", "3", "4", "5", "6"):
        for page in range(1, max_pages + 1):
            try:
                r = requests.get(
                    JJGG_URL,
                    params={"fundcode": FUND_CODE, "pageIndex": page,
                            "pageSize": 50, "type": typ},
                    headers={"User-Agent": "Mozilla/5.0",
                             "Referer": "https://fundf10.eastmoney.com/"},
                    timeout=15,
                )
                items = r.json().get("Data") or []
                if not items:
                    break
                for it in items:
                    aid = it.get("ID")
                    title = (it.get("TITLE") or "").strip()
                    date = (it.get("PUBLISHDATE") or "")[:10]
                    if aid and title and date:
                        seen[aid] = {"id": aid, "date": date, "title": title}
                time.sleep(0.3)
            except Exception:
                break

    out = []
    for rec in seen.values():
        t = rec["title"]
        if not POLICY_KW.search(t):
            continue
        if ROUTINE_KW.search(t) or NOISE_KW.search(t):
            continue
        # 必须指向本基金
        if not re.search(r"纳斯达克100|161130", t):
            continue
        if re.search(r"恢复申购", t):
            kind = "resume"
        elif re.search(r"暂停申购(?!.*大额)", t) and not re.search(r"暂停大额申购", t):
            kind = "suspend"
        else:
            kind = "limit_adjust"
        out.append({**rec, "kind": kind})

    out.sort(key=lambda x: x["date"])
    return out


def _load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {"seen_ids": [], "last_check": None}


def _save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2))


def check(initialize: bool = False) -> dict:
    """
    检查是否有新的申购政策公告

    Args:
        initialize: True 表示首次运行，只记录基线、不报变更

    Returns:
        {"ok":True, "new":[...], "latest":{...}, "current_kind": "suspend"|"resume"}
    """
    announces = fetch_policy_announcements()
    if not announces:
        return {"ok": False, "reason": "未获取到公告"}

    state = _load_state()
    seen = set(state.get("seen_ids") or [])
    first_run = not seen

    new_items = [] if (initialize or first_run) else [
        a for a in announces if a["id"] not in seen
    ]

    state["seen_ids"] = sorted({a["id"] for a in announces} | seen)
    state["last_check"] = datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S")
    _save_state(state)

    latest = announces[-1]
    if not new_items:
        status = "基线已建立（无变更）" if first_run else "无新增政策公告"
    else:
        _kw = {"resume": "🟢 恢复申购", "suspend": "🔴 暂停申购", "limit_adjust": "🟡 调整大额限制"}
        status = " | ".join(f"{_kw.get(a['kind'], a['kind'])}: {a['date']}" for a in new_items)

    return {
        "ok": True,
        "new": new_items,
        "latest": latest,
        "status": status,
        "initialized": first_run,
        "current_kind": latest["kind"],
    }


def watch_line() -> str:
    """给报告用的一行摘要"""
    r = check()
    if not r.get("ok"):
        return f"⚠️ 政策监控不可用（{r.get('reason')}）"
    latest = r["latest"]
    _label = {"resume": "申购开放", "suspend": "暂停申购", "limit_adjust": "限额调整"}
    line = f"{_label.get(latest['kind'], latest['kind'])}（最近变更 {latest['date']}）"
    if r["new"]:
        line += f"  ⚠️ 检测到新公告: {r['status']}"
    return line


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "--init":
        print(check(initialize=True))
    else:
        r = check()
        print(json.dumps({k: v for k, v in r.items() if k != "new"},
                         ensure_ascii=False, indent=2))
        if r.get("new"):
            print("\n新增公告:")
            for a in r["new"]:
                print(f"  {a['date']}  [{a['kind']}]  {a['title'][:80]}")
