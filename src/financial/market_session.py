#!/usr/bin/env python3
"""
A股交易时段判定

背景：
  本系统的定时任务跑在用户自己的机器上（WSL），开机时间不确定。
  OpenClaw 的 cron 默认会对离线期间错过的任务做补跑（skipMissedJobs=false），
  因此报告可能在任何时刻生成——而不是只在 08:15。

  这带来一个被忽视的正确性问题：
    「前收盘价」在盘前指昨日收盘，但盘中/午间生成时，行情源返回的最新价
    已经是当日的盘中价。此时再把它叫「前收盘价」、把偏离率叫「盘前参考偏离率」，
    就是指标名称与实际含义不符。

  因此报告必须知道自己生成在哪个时段，并据此选择正确的措辞。

交易时段（北京时间）：
  盘前     < 09:30
  上午盘   09:30 - 11:30
  午间休市 11:30 - 13:00
  下午盘   13:00 - 15:00
  盘后     > 15:00
"""

from datetime import datetime, timezone, timedelta

CST = timezone(timedelta(hours=8))

# (phase, 起始时分, 结束时分, 中文名)
_SESSIONS = [
    ("pre_market", None, (9, 30), "盘前"),
    ("morning", (9, 30), (11, 30), "上午盘"),
    ("lunch_break", (11, 30), (13, 0), "午间休市"),
    ("afternoon", (13, 0), (15, 0), "下午盘"),
    ("post_market", (15, 0), None, "盘后"),
]

_META = {
    "pre_market": {
        "title": "盘前交易简报",
        "price_role": "prev_close",
        "price_label": "前收盘价",
        "deviation_label": "盘前参考偏离率",
        "applicable": "本报告于开盘前生成，适用于今日全天交易。",
    },
    "morning": {
        "title": "盘中简报（上午盘）",
        "price_role": "intraday",
        "price_label": "最新价（盘中）",
        "deviation_label": "盘中参考偏离率",
        "applicable": "本报告于上午盘生成，行情为盘中实时价，非昨日收盘。",
    },
    "lunch_break": {
        "title": "午间简报（适用于下午盘）",
        "price_role": "intraday",
        "price_label": "上午收盘价",
        "deviation_label": "盘中参考偏离率",
        "applicable": "本报告于午间休市生成（补跑），上午盘已结束，适用于下午盘 13:00-15:00。",
    },
    "afternoon": {
        "title": "盘中简报（下午盘）",
        "price_role": "intraday",
        "price_label": "最新价（盘中）",
        "deviation_label": "盘中参考偏离率",
        "applicable": "本报告于下午盘生成，行情为盘中实时价，非昨日收盘。",
    },
    "post_market": {
        "title": "盘后简报",
        "price_role": "close",
        "price_label": "今日收盘价",
        "deviation_label": "收盘参考偏离率",
        "applicable": "本报告于收盘后生成（补跑），仅供复盘与次日准备，当日已无法交易。",
    },
}


def _minutes(t) -> int:
    return t[0] * 60 + t[1]


def get_session(now: datetime = None) -> dict:
    """
    返回当前所处交易时段的完整描述

    Returns:
        {
          "phase": "lunch_break",
          "phase_cn": "午间休市",
          "title": "午间简报（适用于下午盘）",
          "price_role": "intraday",
          "price_label": "上午收盘价",
          "deviation_label": "盘中参考偏离率",
          "applicable": "...",
          "generated_at": "2026-09-21 12:10",
        }
    """
    now = now or datetime.now(CST)
    cur = now.hour * 60 + now.minute

    phase = "post_market"
    for key, start, end, cn in _SESSIONS:
        if start is None and cur < _minutes(end):
            phase = key
            break
        if start is not None and end is None and cur >= _minutes(start):
            phase = key
            break
        if start is not None and end is not None and _minutes(start) <= cur < _minutes(end):
            phase = key
            break

    cn = next(s[3] for s in _SESSIONS if s[0] == phase)
    meta = dict(_META[phase])
    meta.update({"phase": phase, "phase_cn": cn,
                 "generated_at": now.strftime("%Y-%m-%d %H:%M")})
    return meta


if __name__ == "__main__":
    s = get_session()
    print(f"当前时段: {s['phase_cn']} ({s['phase']})")
    print(f"报告标题: {s['title']}")
    print(f"价格称谓: {s['price_label']}")
    print(f"偏离率称谓: {s['deviation_label']}")
    print(f"适用说明: {s['applicable']}")
    print()
    print("--- 全天时段推演 ---")
    for h, m in [(8, 15), (9, 45), (12, 10), (14, 0), (20, 0)]:
        t = datetime(2026, 9, 21, h, m, tzinfo=CST)
        x = get_session(t)
        print(f"  {h:02d}:{m:02d} → {x['phase_cn']:6s} | {x['title']}")
