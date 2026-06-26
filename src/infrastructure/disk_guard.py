#!/usr/bin/env python3
"""Disk Guard — 磁盘守护脚本

每周自动清理：
  1. 30 天前的 session 文件
  2. 7 天前的情报雷达 raw_data

检查点：
  - 磁盘超 85% → 写入告警文件，HEARTBEAT 会捕获并推送

用法：
  python3 disk_guard.py --clean    # 执行清理
  python3 disk_guard.py --check    # 只检查不清理
  python3 disk_guard.py            # 默认：检查 + 清理
"""

import os
import sys
import json
import shutil
from datetime import datetime, timedelta

WORKSPACE = '/root/.openclaw/workspace'
SESSIONS_DIR = '/root/.openclaw/agents/main/sessions'
RAWDATADIR = f'{WORKSPACE}/skills/analysis/ai-intelligence-radar/raw_data'
REPORTS_DIR = f'{WORKSPACE}/skills/analysis/ai-intelligence-radar/reports'
ALERT_FILE = f'{WORKSPACE}/.alerts/disk_alert.json'

# 阈值
DISK_THRESHOLD = 85  # 磁盘使用率告警线
SESSION_KEEP_DAYS = 30
RAWDATAKEEPDAYS = 7
REPORT_KEEP_DAYS = 7


def get_disk_usage():
    """获取磁盘使用率"""
    stat = shutil.disk_usage('/')
    used_percent = int((stat.used / stat.total) * 100)
    return {
        'total_gb': round(stat.total / (1024**3), 1),
        'used_gb': round(stat.used / (1024**3), 1),
        'free_gb': round(stat.free / (1024**3), 1),
        'used_percent': used_percent
    }


def clean_sessions(dry_run=False):
    """清理 30 天前的 session 文件"""
    if not os.path.exists(SESSIONS_DIR):
        return {'cleaned': 0, 'size_mb': 0}
    
    cutoff = datetime.now() - timedelta(days=SESSION_KEEP_DAYS)
    cleaned = 0
    size_freed = 0
    
    for filename in os.listdir(SESSIONS_DIR):
        if not filename.endswith('.jsonl'):
            continue
        filepath = os.path.join(SESSIONS_DIR, filename)
        mtime = datetime.fromtimestamp(os.path.getmtime(filepath))
        
        if mtime < cutoff:
            size = os.path.getsize(filepath)
            if not dry_run:
                os.remove(filepath)
            cleaned += 1
            size_freed += size
    
    return {
        'cleaned': cleaned,
        'size_mb': round(size_freed / (1024**2), 1)
    }


def clean_raw_data(dry_run=False):
    """清理 7 天前的情报雷达原始数据"""
    if not os.path.exists(RAWDATADIR):
        return {'cleaned': 0, 'size_mb': 0}
    
    cutoff = datetime.now() - timedelta(days=RAWDATAKEEPDAYS)
    cleaned = 0
    size_freed = 0
    
    for filename in os.listdir(RAWDATADIR):
        if not filename.endswith('.json'):
            continue
        filepath = os.path.join(RAWDATADIR, filename)
        mtime = datetime.fromtimestamp(os.path.getmtime(filepath))
        
        if mtime < cutoff:
            size = os.path.getsize(filepath)
            if not dry_run:
                os.remove(filepath)
            cleaned += 1
            size_freed += size
    
    return {
        'cleaned': cleaned,
        'size_mb': round(size_freed / (1024**2), 1)
    }


def clean_old_reports(dry_run=False):
    """清理 7 天前的报告文件"""
    if not os.path.exists(REPORTS_DIR):
        return {'cleaned': 0, 'size_mb': 0}
    
    cutoff = datetime.now() - timedelta(days=REPORT_KEEP_DAYS)
    cleaned = 0
    size_freed = 0
    
    for filename in os.listdir(REPORTS_DIR):
        if not (filename.startswith('report_') and filename.endswith('.md')):
            continue
        filepath = os.path.join(REPORTS_DIR, filename)
        mtime = datetime.fromtimestamp(os.path.getmtime(filepath))
        
        if mtime < cutoff:
            size = os.path.getsize(filepath)
            if not dry_run:
                os.remove(filepath)
            cleaned += 1
            size_freed += size
    
    return {
        'cleaned': cleaned,
        'size_mb': round(size_freed / (1024**2), 1)
    }


def write_alert(disk_info, reason):
    """写入告警文件"""
    os.makedirs(os.path.dirname(ALERT_FILE), exist_ok=True)
    alert = {
        'type': 'disk_warning',
        'timestamp': datetime.now().isoformat(),
        'disk_used_percent': disk_info['used_percent'],
        'disk_free_gb': disk_info['free_gb'],
        'reason': reason,
        'action_needed': '手动检查大文件或扩容'
    }
    with open(ALERT_FILE, 'w') as f:
        json.dump(alert, f, indent=2, ensure_ascii=False)
    return alert


def clear_alert():
    """清除告警文件"""
    if os.path.exists(ALERT_FILE):
        os.remove(ALERT_FILE)


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Disk Guard')
    parser.add_argument('--clean', action='store_true', help='执行清理')
    parser.add_argument('--check', action='store_true', help='只检查不清理')
    args = parser.parse_args()
    
    # 默认行为：检查 + 清理
    do_clean = args.clean or (not args.check)
    dry_run = args.check
    
    # 1. 检查磁盘
    disk = get_disk_usage()
    print(f"磁盘状态: {disk['used_gb']}G / {disk['total_gb']}G ({disk['used_percent']}%)")
    
    # 2. 如果超阈值，写告警
    if disk['used_percent'] >= DISK_THRESHOLD:
        reason = f"磁盘使用率 {disk['used_percent']}% >= {DISK_THRESHOLD}%"
        alert = write_alert(disk, reason)
        print(f"⚠️  {reason}，已写入告警")
    else:
        clear_alert()
        print(f"✅ 磁盘正常 ({disk['used_percent']}%)，已清除旧告警")
    
    # 3. 清理 session
    session_result = clean_sessions(dry_run=dry_run)
    action = "[DRY-RUN] 将清理" if dry_run else "已清理"
    print(f"{action} {session_result['cleaned']} 个旧 session，释放 {session_result['size_mb']}MB")
    
    # 4. 清理 raw_data
    raw_result = clean_raw_data(dry_run=dry_run)
    print(f"{action} {raw_result['cleaned']} 个旧 raw_data，释放 {raw_result['size_mb']}MB")
    
    # 5. 清理旧报告
    report_result = clean_old_reports(dry_run=dry_run)
    print(f"{action} {report_result['cleaned']} 个旧报告，释放 {report_result['size_mb']}MB")
    
    # 6. 汇总
    total_freed = session_result['size_mb'] + raw_result['size_mb'] + report_result['size_mb']
    print(f"\n总计释放: {total_freed}MB")
    
    # 返回码：磁盘告警 = 1，正常 = 0
    return 1 if disk['used_percent'] >= DISK_THRESHOLD else 0


if __name__ == '__main__':
    sys.exit(main())
