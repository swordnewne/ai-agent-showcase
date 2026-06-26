#!/usr/bin/env python3
"""
微信推送执行器 - 读取pending_alerts并发送微信消息

用法:
  python wechat_pusher.py          # 推送所有待发送消息
  python wechat_pusher.py --dry    # 仅预览，不发送
"""

import os
import sys
import json
import argparse
from datetime import datetime, timezone, timedelta

WORKSPACE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PENDING_FILE = os.path.join(WORKSPACE, ".alerts", "pending_wechat.json")
SENT_FILE = os.path.join(WORKSPACE, ".alerts", "sent_wechat.json")


def load_pending() -> list:
    """加载待发送消息"""
    if not os.path.exists(PENDING_FILE):
        return []
    try:
        with open(PENDING_FILE, "r") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except:
        return []


def mark_sent(alerts: list):
    """标记已发送"""
    sent = []
    if os.path.exists(SENT_FILE):
        try:
            with open(SENT_FILE, "r") as f:
                sent = json.load(f)
        except:
            pass
    
    sent.extend(alerts)
    # 只保留最近100条
    sent = sent[-100:]
    
    with open(SENT_FILE, "w") as f:
        json.dump(sent, f, ensure_ascii=False, indent=2)
    
    # 清空pending
    with open(PENDING_FILE, "w") as f:
        json.dump([], f)


def send_via_openclaw(message: str) -> bool:
    """通过OpenClaw发送微信消息
    
    实际调用方式:
    openclaw message send --message "内容" --channel openclaw-weixin
    """
    import subprocess
    try:
        cmd = [
            "openclaw", "message", "send",
            "--message", message,
            "--channel", "openclaw-weixin"
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            return True
        else:
            print(f"[发送失败] {result.stderr}")
            return False
    except Exception as e:
        print(f"[发送异常] {e}")
        return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry", action="store_true", help="仅预览，不发送")
    args = parser.parse_args()
    
    alerts = load_pending()
    if not alerts:
        print("没有待发送消息")
        return
    
    print(f"发现 {len(alerts)} 条待发送消息")
    
    sent_count = 0
    for alert in alerts:
        msg = alert.get("message", "")
        if not msg:
            continue
        
        print(f"\n{'='*50}")
        print(msg)
        print(f"{'='*50}")
        
        if not args.dry:
            if send_via_openclaw(msg):
                sent_count += 1
                print("✅ 已发送")
            else:
                print("❌ 发送失败")
        else:
            print("[DRY RUN] 未发送")
    
    if not args.dry:
        mark_sent(alerts)
        print(f"\n发送完成: {sent_count}/{len(alerts)}")


if __name__ == "__main__":
    main()
