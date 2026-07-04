#!/usr/bin/env python3
"""
定时任务健康检查 + 自主修复脚本
每天 03:00 运行（在记忆整理之前），检查所有定时任务的健康状态，自动修复常见问题。

检查项：
1. 所有 cron 任务是否最近执行过
2. 脚本是否有错误日志
3. 依赖是否缺失
4. 文件权限是否正确
5. 磁盘/内存是否充足
"""

import os, json, glob, re, datetime, subprocess

LEARNING_DIR = '/root/.openclaw/workspace/.learnings'
SELF_HEALING_LOG = f'{LEARNING_DIR}/self-healing-patterns.json'
LOGS_DIR = '/tmp'

# 要检查的定时任务
cron_checks = [
    {
        'name': 'memory_consolidation',
        'log': '/tmp/memory_consolidation.log',
        'script': '/root/.openclaw/workspace/scripts/memory_consolidation.py',
        'pattern': r'consolidation',
    },
    {
        'name': 'financial_news_daily',
        'log': '/tmp/financial_news/crawl_run.log',
        'script': '/tmp/financial_news/guard.sh',
        'pattern': r'guard\.sh.*daily',
    },
    {
        'name': 'financial_news_sentinel',
        'log': '/tmp/financial_news/sentinel.log',
        'script': '/tmp/financial_news/sentinel.py',
        'pattern': r'sentinel',
    },
    {
        'name': 'meyo_heartbeat_morning',
        'log': '/tmp/meyo_heartbeat_morning.log',
        'script': None,  # HEARTBEAT 触发
        'pattern': r'heartbeat',
    },
    {
        'name': 'meyo_heartbeat_evening',
        'log': '/tmp/meyo_heartbeat_evening.log',
        'script': None,
        'pattern': r'heartbeat',
    },
    {
        'name': 'meyo_post_daily',
        'log': '/tmp/meyo_post.log',
        'script': '/root/.openclaw/workspace/scripts/meyo_post_daily.py',
        'pattern': r'meyo_post',
    },
    {
        'name': 'meyo_post_morning',
        'log': '/tmp/meyo_post_morning.log',
        'script': '/root/.openclaw/workspace/scripts/meyo_post_daily.py',
        'pattern': r'meyo_post',
    },
    {
        'name': 'meyo_ab_tracker',
        'log': '/tmp/meyo_ab_retro.log',
        'script': '/root/.openclaw/workspace/scripts/meyo_ab_tracker.py',
        'pattern': r'A/B',
    },
]

def record_self_healing(error_type, symptom, fix, script=None, success=True):
    entry = {
        'timestamp': datetime.datetime.now().isoformat(),
        'error_type': error_type,
        'symptom': symptom,
        'fix': fix,
        'script': script or 'health_check.py',
        'auto': True,
        'success': success,
    }
    
    patterns = []
    if os.path.exists(SELF_HEALING_LOG):
        try:
            with open(SELF_HEALING_LOG, 'r', encoding='utf-8') as f:
                patterns = json.load(f)
        except Exception:
            patterns = []
    
    patterns.append(entry)
    patterns = patterns[-100:]
    
    os.makedirs(LEARNING_DIR, exist_ok=True)
    with open(SELF_HEALING_LOG, 'w', encoding='utf-8') as f:
        json.dump(patterns, f, ensure_ascii=False, indent=2)
    
    print(f'[自主修复] {error_type}: {fix} (success={success})')

def check_disk_space():
    """检查磁盘空间，不足时自动清理"""
    try:
        result = subprocess.run(['df', '-h', '/'], capture_output=True, text=True, timeout=10)
        lines = result.stdout.strip().split('\n')
        if len(lines) >= 2:
            parts = lines[1].split()
            usage = parts[4] if len(parts) > 4 else '0%'
            usage_pct = int(usage.replace('%', ''))
            
            if usage_pct >= 80:
                print(f'[磁盘] 使用率 {usage_pct}%，触发清理')
                # 清理日志
                subprocess.run(['journalctl', '--vacuum-time=1d'], capture_output=True, timeout=30)
                subprocess.run(['find', '/tmp', '-type', 'f', '-mtime', '+7', '-delete'], capture_output=True, timeout=30)
                
                # 重新检查
                result2 = subprocess.run(['df', '-h', '/'], capture_output=True, text=True, timeout=10)
                lines2 = result2.stdout.strip().split('\n')
                if len(lines2) >= 2:
                    parts2 = lines2[1].split()
                    usage2 = int(parts2[4].replace('%', '')) if len(parts2) > 4 else 0
                    
                    record_self_healing(
                        'disk_space_low',
                        f'磁盘使用率 {usage_pct}%',
                        f'journalctl + 清理 /tmp，降至 {usage2}%',
                        success=usage2 < 80
                    )
                    return usage2 < 80
            return True
    except Exception as e:
        print(f'[磁盘检查] 错误: {e}')
        return False

def check_memory():
    """检查内存，不足时尝试释放"""
    try:
        with open('/proc/meminfo', 'r') as f:
            meminfo = f.read()
        
        mem_total = int(re.search(r'MemTotal:\s+(\d+)', meminfo).group(1)) * 1024
        mem_available = int(re.search(r'MemAvailable:\s+(\d+)', meminfo).group(1)) * 1024
        
        usage_pct = (1 - mem_available / mem_total) * 100
        
        if usage_pct >= 85:
            print(f'[内存] 使用率 {usage_pct:.1f}%，尝试释放')
            # 清理缓存
            subprocess.run(['sync'], capture_output=True, timeout=10)
            subprocess.run(['echo', '3', '>', '/proc/sys/vm/drop_caches'], capture_output=True, timeout=10)
            
            # 重新检查
            with open('/proc/meminfo', 'r') as f:
                meminfo2 = f.read()
            mem_available2 = int(re.search(r'MemAvailable:\s+(\d+)', meminfo2).group(1)) * 1024
            usage_pct2 = (1 - mem_available2 / mem_total) * 100
            
            record_self_healing(
                'memory_low',
                f'内存使用率 {usage_pct:.1f}%',
                f'drop_caches 释放，降至 {usage_pct2:.1f}%',
                success=usage_pct2 < 85
            )
            return usage_pct2 < 85
        return True
    except Exception as e:
        print(f'[内存检查] 错误: {e}')
        return False

def check_task_health(task):
    """检查单个定时任务的健康状态"""
    name = task['name']
    log_file = task['log']
    script = task['script']
    
    print(f'[检查] {name}')
    
    # 1. 检查日志文件是否存在且最近更新
    if log_file and os.path.exists(log_file):
        mtime = os.path.getmtime(log_file)
        hours_ago = (datetime.datetime.now().timestamp() - mtime) / 3600
        
        if hours_ago > 48:
            print(f'  ⚠️ 日志超过 48 小时未更新 ({hours_ago:.1f}h)')
            record_self_healing(
                'task_stale',
                f'{name} 日志 {hours_ago:.1f}h 未更新',
                '检查 cron 配置和脚本状态',
                success=False
            )
            return False
        
        # 2. 检查日志中的错误
        try:
            with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
                log_content = f.read()[-5000:]  # 最后 5000 字符
            
            errors = re.findall(r'(ERROR|Exception|Traceback|Failed)', log_content, re.IGNORECASE)
            if len(errors) > 5:
                print(f'  ⚠️ 最近日志有 {len(errors)} 处错误')
                record_self_healing(
                    'task_errors',
                    f'{name} 日志有 {len(errors)} 处错误',
                    '检查错误详情并修复',
                    success=False
                )
                return False
        except Exception:
            pass
    else:
        if log_file:
            print(f'  ⚠️ 日志文件不存在: {log_file}')
    
    # 3. 检查脚本是否存在且可执行
    if script:
        if not os.path.exists(script):
            print(f'  ❌ 脚本不存在: {script}')
            record_self_healing(
                'script_missing',
                f'{name} 脚本缺失: {script}',
                '需要重新部署或检查路径',
                success=False
            )
            return False
        
        if not os.access(script, os.X_OK) and script.endswith('.sh'):
            print(f'  ⚠️ 脚本不可执行，尝试 chmod +x')
            try:
                os.chmod(script, 0o755)
                record_self_healing(
                    'script_not_executable',
                    f'{name} 脚本无执行权限',
                    'chmod +x 已修复',
                    success=True
                )
            except Exception:
                record_self_healing(
                    'script_not_executable',
                    f'{name} 脚本无执行权限',
                    'chmod 失败',
                    success=False
                )
                return False
    
    print(f'  ✅ 正常')
    return True

def check_ssh_key():
    """检查 GitHub SSH 密钥一致性
    
    防止密钥路径变更、文件丢失、指纹不匹配导致推送失败。
    规则：
    - 私钥必须存在且指纹匹配 GitHub 绑定的 niko 密钥
    - SSH config 必须指向正确私钥 + IdentitiesOnly yes
    """
    KEY_PATH = os.path.expanduser('~/.ssh/github_niko_key')
    EXPECTED_FINGERPRINT = 'SHA256:1eEwOsNMk/7CH445+Y4Wt/JSJYdfUjaQECmLjFG7i28'
    
    # 1. 文件存在性
    if not os.path.exists(KEY_PATH):
        return False, f'私钥文件不存在: {KEY_PATH}'
    
    # 2. 指纹校验
    try:
        result = subprocess.run(
            ['ssh-keygen', '-l', '-f', KEY_PATH],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode != 0 or EXPECTED_FINGERPRINT not in result.stdout:
            return False, f'私钥指纹不匹配 (预期 {EXPECTED_FINGERPRINT[:20]}...)'
    except Exception as e:
        return False, f'指纹校验失败: {e}'
    
    # 3. SSH config 检查
    config_path = os.path.expanduser('~/.ssh/config')
    if os.path.exists(config_path):
        with open(config_path, 'r') as f:
            cfg = f.read()
        if 'github_niko_key' not in cfg:
            return False, 'SSH config 未指向 github_niko_key'
        if 'IdentitiesOnly yes' not in cfg:
            return False, 'SSH config 缺少 IdentitiesOnly yes'
    else:
        return False, 'SSH config 文件不存在'
    
    return True, 'OK'


def main():


    print('=' * 50)
    print('🔍 定时任务健康检查 + 自主修复')
    print(f'时间: {datetime.datetime.now().isoformat()}')
    print('=' * 50)
    
    # 1. 检查资源
    print()
    print('【资源检查】')
    disk_ok = check_disk_space()
    mem_ok = check_memory()
    print(f'  磁盘: {"✅ 正常" if disk_ok else "⚠️ 需关注"}')
    print(f'  内存: {"✅ 正常" if mem_ok else "⚠️ 需关注"}')
    
    # 2. 检查所有定时任务
    print()
    print('【任务检查】')
    healthy = 0
    unhealthy = 0
    for task in cron_checks:
        if check_task_health(task):
            healthy += 1
        else:
            unhealthy += 1
    
    # 3. 检查觅游凭证
    print()
    print('【凭证检查】')
    creds_path = '/root/.openclaw/meyo/credentials.json'
    if os.path.exists(creds_path):
        try:
            with open(creds_path, 'r') as f:
                creds = json.load(f)
            if creds.get('api_key'):
                print('  ✅ 觅游凭证有效')
            else:
                print('  ❌ 觅游凭证缺失 API Key')
                unhealthy += 1
        except Exception:
            print('  ❌ 觅游凭证读取失败')
            unhealthy += 1
    else:
        print('  ❌ 觅游凭证文件不存在')
        unhealthy += 1
    
    # 4. 检查 SSH 密钥一致性（防止推送失败）
    print()
    print('【SSH密钥检查】')
    ssh_ok, ssh_msg = check_ssh_key()
    if ssh_ok:
        print('  ✅ GitHub SSH密钥一致')
    else:
        print(f'  ❌ {ssh_msg}')
        unhealthy += 1
        record_self_healing(
            'ssh_key_mismatch',
            ssh_msg,
            '检查 ~/.ssh/github_niko_key 是否存在且指纹匹配',
            success=False
        )
    
    # 5. 总结
    print()
    print('=' * 50)
    print(f'检查结果: ✅ 正常 {healthy} | ⚠️ 异常 {unhealthy}')
    print('=' * 50)
    
    return 0 if unhealthy == 0 else 1

if __name__ == '__main__':
    import sys
    sys.exit(main())
