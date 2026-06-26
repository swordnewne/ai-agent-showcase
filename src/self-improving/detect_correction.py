#!/usr/bin/env python3
"""纠正检测后处理器（Instinct 系统入口）

每次交互后自动检查用户消息是否包含纠正关键词，
如果包含，自动调用 instinct_system.py 记录。

集成方式：
  - 在回复用户前/后，调用 detect_and_record(message, context)
  - 或 HEARTBEAT 检查时扫描最近对话

用法：
  python3 detect_correction.py --message "不对，应该是纯中文" --context "之前的回复"
  python3 detect_correction.py --check-last  # 检查最近对话
"""

import os
import sys
import json
import subprocess
from datetime import datetime

WORKSPACE = '/root/.openclaw/workspace'
INSTINCT_SCRIPT = f'{WORKSPACE}/scripts/instinct_system.py'

# 纠正关键词表（可扩展）
CORRECTION_KEYWORDS = [
    # 直接纠正
    '不对', '错了', '不是', '应该是', '更正', '纠正', '搞错了',
    '你错了', '说错了', '理解错了', '搞混了',
    # 风格纠正
    '不要中英文', '不要混用', '纯中文', '英文太多', '别用英文',
    # 深度纠正
    '太简单', '不够深入', '详细一点', '展开讲讲', '太浅了',
    # 格式纠正
    '分页', '行间距', '字体', '排版', '格式不对', '重新排',
    # 质量纠正
    '修一下', '改一下', '重做', '重排', '重新写', '不对味',
    # 方向纠正
    '跑题了', '偏了', '不是这个', '我问的是',
]

# 更严重的纠正（高优先级）
STRONG_CORRECTION = [
    '彻底错了', '完全不对', '你在胡说什么', '重来',
    '我说的是', '我要的是', '不是你这样的',
]


def is_correction(message):
    """判断消息是否为纠正"""
    if not message:
        return False, None
    
    text = message.lower()
    matched = []
    
    for kw in CORRECTION_KEYWORDS:
        if kw in text:
            matched.append(kw)
    
    # 检查是否包含强纠正
    is_strong = any(kw in text for kw in STRONG_CORRECTION)
    
    return len(matched) > 0, {
        'keywords': matched,
        'strong': is_strong,
        'text': message
    }


def extract_correction_pattern(user_msg, assistant_msg):
    """从纠正中提取旧做法 vs 新做法
    
    尝试解析：
      "不要 A，应该是 B" → 旧=A, 新=B
      "不对，A 应该是 B" → 旧=A, 新=B
      "错了，不是 A 是 B" → 旧=A, 新=B
    """
    text = user_msg.lower()
    
    # 模式：不是...是... / 不要...要... / 应该是...
    patterns = [
        r'不是(.+?)是(.+)',
        r'不要(.+?)要(.+)',
        r'应该是(.+)而不是(.+)',
        r'(.+?)错了，应该是(.+)',
    ]
    
    for pattern in patterns:
        import re
        match = re.search(pattern, text)
        if match:
            return {
                'old': match.group(1).strip()[:100],
                'new': match.group(2).strip()[:100],
                'type': 'explicit'
            }
    
    # 如果解析不出结构，提取关键词
    return {
        'old': '之前做法',
        'new': user_msg[:200],
        'type': 'implicit'
    }


def record_correction(user_msg, assistant_msg=''):
    """记录纠正到 Instinct 系统"""
    is_corr, info = is_correction(user_msg)
    
    if not is_corr:
        return {'recorded': False, 'reason': '未检测到纠正关键词'}
    
    # 提取纠正模式
    pattern = extract_correction_pattern(user_msg, assistant_msg)
    
    # 构造记录文本
    record_text = f"{user_msg[:150]}"
    context = f"之前回复: {assistant_msg[:200]} | 提取: {pattern['old']} → {pattern['new']}"
    
    # 调用 instinct_system.py
    try:
        result = subprocess.run(
            [sys.executable, INSTINCT_SCRIPT, '--record', record_text, '--context', context],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        output = result.stdout.strip()
        
        return {
            'recorded': True,
            'keywords': info['keywords'],
            'strong': info['strong'],
            'pattern': pattern,
            'instinct_output': output
        }
    except Exception as e:
        return {
            'recorded': False,
            'reason': f'调用失败: {e}',
            'keywords': info['keywords']
        }


def check_last_conversation():
    """检查最近对话中的纠正（用于 HEARTBEAT）"""
    # 读取 memory/2026-06-25.md 或最近的对话记录
    today = datetime.now().strftime('%Y-%m-%d')
    diary = f'{WORKSPACE}/memory/{today}.md'
    
    if not os.path.exists(diary):
        return {'checked': False, 'reason': '今日日记不存在'}
    
    with open(diary, 'r') as f:
        content = f.read()
    
    # 简单检查：最近 1000 字是否含纠正关键词
    recent = content[-2000:]
    is_corr, info = is_correction(recent)
    
    if is_corr:
        return {
            'checked': True,
            'found': True,
            'keywords': info['keywords'],
            'suggestion': '检测到可能的纠正，建议手动 review 最近对话'
        }
    
    return {'checked': True, 'found': False}


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='纠正检测器')
    parser.add_argument('--message', '-m', help='用户消息')
    parser.add_argument('--context', '-c', default='', help='之前的回复（可选）')
    parser.add_argument('--check-last', action='store_true', help='检查最近对话')
    parser.add_argument('--test', action='store_true', help='测试模式：列出所有关键词')
    args = parser.parse_args()
    
    if args.test:
        print("纠正关键词表:")
        for kw in CORRECTION_KEYWORDS:
            print(f"  - {kw}")
        print(f"\n强纠正关键词:")
        for kw in STRONG_CORRECTION:
            print(f"  - {kw}")
        return
    
    if args.check_last:
        result = check_last_conversation()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    
    if args.message:
        result = record_correction(args.message, args.context)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        
        if result['recorded']:
            print(f"\n✅ 已记录纠正 (关键词: {', '.join(result['keywords'])})")
            if result.get('strong'):
                print("⚠️  强纠正，优先处理")
    else:
        # 标准输入模式
        print("输入用户消息 (Ctrl+D 结束):")
        message = sys.stdin.read().strip()
        if message:
            result = record_correction(message)
            print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
