#!/usr/bin/env python3
"""
消息推送工具 - 纯读取/预览工具（消费者已迁移为 agent-native cron）

用法:
  python alert_pusher.py          # 预览待发送消息
  python alert_pusher.py --clear  # 清空 pending 文件（手动兜底）
"""

import os
import json

WORKSPACE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PENDING_FILE = os.path.join(WORKSPACE, ".alerts", "pending_alerts.json")


def load_pending() -> list:
    """加载待发送消息"""
    if not os.path.exists(PENDING_FILE):
        return []
    try:
        with open(PENDING_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--clear", action="store_true", help="清空 pending 文件")
    args = parser.parse_args()

    alerts = load_pending()
    print(f"pending_alerts.json: {len(alerts)} 条消息")

    if not alerts:
        return

    for i, alert in enumerate(alerts[:5]):
        msg = alert.get("msg") or alert.get("message", "")
        print(f"\n[{i+1}] {msg[:80]}...")

    if len(alerts) > 5:
        print(f"\n... 还有 {len(alerts)-5} 条")

    if args.clear:
        with open(PENDING_FILE, "w", encoding="utf-8") as f:
            json.dump([], f)
        print(f"\n✅ 已清空 {PENDING_FILE}")


if __name__ == "__main__":
    main()
