#!/usr/bin/env python3
"""
金融信号预警系统 - 启动器
用法:
  python launcher.py realtime    # 交易时段实时检测
  python launcher.py summary     # 收盘后日终总结
"""

import os
import sys
import subprocess
from datetime import datetime, timezone, timedelta

BASE = os.path.dirname(os.path.abspath(__file__))

def run(cmd):
    print(f"[RUN] {cmd}")
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)
    return result.returncode == 0

def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "realtime"
    now = datetime.now(timezone(timedelta(hours=8)))
    print(f"\n{'='*60}")
    print(f"金融信号预警系统启动 [{mode}] {now.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}\n")
    
    # 1. 运行信号检测
    pipeline = os.path.join(BASE, "signal_pipeline.py")
    ok = run(f"python3 {pipeline} --mode {mode}")
    
    if not ok:
        print("[WARN] 信号检测未正常完成")
    
    # 2. 推送微信消息
    pusher = os.path.join(BASE, "wechat_pusher.py")
    run(f"python3 {pusher}")
    
    print(f"\n{'='*60}")
    print(f"运行完成 {now.strftime('%H:%M:%S')}")
    print(f"{'='*60}\n")

if __name__ == "__main__":
    main()
