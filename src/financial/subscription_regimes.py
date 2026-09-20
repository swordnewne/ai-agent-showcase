#!/usr/bin/env python3
"""
申购制度分段 —— 让分位点在同一制度内比较

为什么需要：
  实证（485 个交易日）显示，申购制度是溢价的强解释变量：

    暂停申购      均值 +2.73%   正溢价占比 85%   (n=164)
    申购开放+限额  均值 +0.64%   正溢价占比 58%   (n=301)
    差异 +2.09pp

  这意味着：用「开放期」的历史分布去衡量「暂停期」的溢价会系统性低估。
  一个在暂停期显得"平平无奇"的 2%，放在开放期已属偏高。

  因此分位点必须在【同制度】样本内计算，否则口径错配。

数据来源：东方财富基金公告接口（见 docs/161130-subscription-policy-analysis.md）
局限性：公告只给变更日期，不给限额数值，因此只能做「暂停 / 开放」二分，
        无法量化「限制程度」。「开放」段实际含「暂停大额申购」。
        另：无法确定因果方向（可能是溢价压力导致基金公司主动暂停申购）。
"""

from datetime import datetime

SUSPENDED = "suspended"
OPEN = "open"

# (起始, 结束, 类型, 说明)  —— 结束为 None 表示至今
REGIMES = [
    ("2020-03-11", "2020-03-18", SUSPENDED, "暂停申购"),
    ("2020-03-19", "2021-01-04", OPEN, "恢复申购（暂停大额）"),
    ("2021-01-05", "2021-05-26", OPEN, "恢复大额申购"),
    ("2021-05-27", "2021-06-03", OPEN, "暂停大额申购"),
    ("2021-06-04", "2021-08-02", OPEN, "恢复大额申购"),
    ("2021-08-03", "2021-09-07", OPEN, "暂停大额申购"),
    ("2021-09-08", "2021-10-11", OPEN, "恢复大额申购"),
    ("2021-10-12", "2022-01-18", OPEN, "暂停大额申购"),
    ("2022-01-19", "2022-03-08", OPEN, "恢复大额申购"),
    ("2022-03-09", "2023-01-04", OPEN, "暂停大额申购"),
    ("2023-01-05", "2024-10-24", SUSPENDED, "暂停申购"),
    ("2024-10-25", "2024-12-24", OPEN, "恢复申购（暂停大额）"),
    ("2024-12-25", "2025-02-24", SUSPENDED, "暂停申购"),
    ("2025-02-25", "2026-03-18", OPEN, "恢复申购（暂停大额）"),
    ("2026-03-19", None, SUSPENDED, "暂停申购（至今）"),
]

LABELS = {SUSPENDED: "暂停申购", OPEN: "申购开放"}


def regime_at(date_str: str) -> dict:
    """
    返回某交易日的申购制度

    Returns:
        {"kind": "suspended", "label": "暂停申购",
         "start": "2026-03-19", "end": None, "note": "暂停申购（至今）"}
    """
    for start, end, kind, note in REGIMES:
        if end is None:
            if date_str >= start:
                return {"kind": kind, "label": LABELS[kind], "start": start,
                        "end": None, "note": note}
        elif start <= date_str <= end:
            return {"kind": kind, "label": LABELS[kind], "start": start,
                    "end": end, "note": note}
    return {"kind": None, "label": "未知", "start": None, "end": None, "note": "早于已知制度区间"}


def regime_span(date_str: str = None) -> tuple:
    """返回当前制度的时间跨度 (start, end)"""
    r = regime_at(date_str or datetime.now().strftime("%Y-%m-%d"))
    return (r["start"], r["end"])


if __name__ == "__main__":
    print(f"{'日期':<14}{'制度':<12}{'起始':<14}{'说明'}")
    print("-" * 60)
    for d in ["2024-10-01", "2024-11-15", "2025-01-15", "2025-06-01",
              "2026-03-01", "2026-03-25", "2026-09-20"]:
        r = regime_at(d)
        print(f"{d:<14}{r['label']:<12}{str(r['start']):<14}{r['note']}")
