#!/usr/bin/env python3
"""
盘前简报兜底检查器

定时任务到点调用（08:15 / 22:00），判定"今天是否已成功生成过盘前简报"：
  - 已 complete → 跳过（严格幂等，不重复生成）
  - 未 complete → 执行 preopen_report

用法:
  python3 preopen_guard.py                 # 检查并条件执行
  python3 preopen_guard.py --status        # 只查状态，不执行
  python3 preopen_guard.py --slot 08:15    # 标记时段
  python3 preopen_guard.py --force         # 强制重跑（覆盖幂等）
  python3 preopen_guard.py --ignore-calendar  # 忽略交易日历（周末/节假日手动跑）

退出码: 0 = 完成或已跳过; 1 = 生成失败/部分失败
"""

import sys
import os
import argparse
from pathlib import Path

WORKSPACE = os.environ.get("NIKO_WORKSPACE") or str(Path(__file__).resolve().parents[3])
sys.path.insert(0, str(Path(WORKSPACE) / "showcase" / "src" / "financial"))

from preopen_runs import today_str, is_done_today, get_today_runs
from trade_calendar import ensure_fresh
import preopen_report


def cmd_status() -> int:
    date = today_str()
    done, path = is_done_today(date)
    print(f"日期: {date}")
    print(f"已完成(complete): {'是' if done else '否'}")
    print(f"报告路径: {path or '(无)'}")
    runs = get_today_runs(date)
    if runs:
        print("今日运行记录:")
        for slot, status, rpath, dstatus, created in runs:
            print(f"  [{slot}] {status} / 投递={dstatus} @ {created}")
    else:
        print("今日运行记录: 无")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="盘前简报兜底检查器")
    ap.add_argument("--status", action="store_true", help="只查状态")
    ap.add_argument("--slot", default="auto", help="时段标记，如 08:15 / 22:00")
    ap.add_argument("--force", action="store_true", help="强制重跑")
    ap.add_argument("--ignore-calendar", action="store_true", help="忽略交易日历")
    args = ap.parse_args()

    if args.status:
        return cmd_status()

    # 交易日历保鲜（失败静默，不阻断主流程）
    try:
        fresh = ensure_fresh(max_age_days=7)
        if fresh.get("refreshed"):
            print(f"[日历] 已从上交所官网刷新 ({fresh.get('count', '?')} 个休市日)")
    except Exception as e:
        print(f"[日历] 保鲜跳过: {e}")

    date = today_str()
    done, path = is_done_today(date)

    if done and not args.force:
        print(f"[SKIP] {date} 已有成功报告，跳过（严格幂等）")
        print(f"报告路径: {path}")
        return 0

    if args.force and done:
        print(f"[FORCE] {date} 已存在成功报告，仍强制重跑")

    print(f"[RUN] 生成盘前简报 ({date}, slot={args.slot})")
    result = preopen_report.run(slot=args.slot, ignore_calendar=args.ignore_calendar)
    status = result.get("status")
    rpath = result.get("report_path")
    print(f"[DONE] status={status} path={rpath or '(无)'}")
    if result.get("reason"):
        print(f"[NOTE] {result['reason']}")

    return 0 if status in ("complete", "skipped") else 1


if __name__ == "__main__":
    sys.exit(main())
