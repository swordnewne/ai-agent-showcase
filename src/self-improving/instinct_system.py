#!/usr/bin/env python3
"""Instinct 系统（ECC Continuous Learning 简化版）

从用户纠正中自动提取模式，形成"本能"，置信度达标后升级为 SKILL.md 规则。

文件结构:
  self-improving/
    instincts/
      raw/          ← 原始本能记录
      evolved/      ← 升级后的技能片段
      index.json    ← 本能索引
    
用法:
  python3 instinct_system.py --record "用户纠正: 不要中英文混用" --context "之前回复"
  python3 instinct_system.py --evolve  # 聚类高置信度本能
  python3 instinct_system.py --status  # 查看本能统计
"""

import os
import re
import json
import hashlib
from datetime import datetime
from collections import defaultdict

WORKSPACE = '/root/.openclaw/workspace'
INSTINCT_DIR = f'{WORKSPACE}/self-improving/instincts'
RAW_DIR = f'{INSTINCT_DIR}/raw'
EVOLVED_DIR = f'{INSTINCT_DIR}/evolved'
INDEX_FILE = f'{INSTINCT_DIR}/index.json'

# 置信度阈值
EVOLVE_THRESHOLD = 3  # 出现 3 次升级为规则


def ensure_dirs():
    os.makedirs(RAW_DIR, exist_ok=True)
    os.makedirs(EVOLVED_DIR, exist_ok=True)


def load_index():
    if os.path.exists(INDEX_FILE):
        with open(INDEX_FILE, 'r') as f:
            return json.load(f)
    return {'instincts': [], 'evolved': [], 'next_id': 1}


def save_index(index):
    with open(INDEX_FILE, 'w') as f:
        json.dump(index, f, indent=2, ensure_ascii=False)


def generate_id(text: str) -> str:
    """生成稳定 ID"""
    return hashlib.md5(text.encode()).hexdigest()[:8]


def extract_pattern(correction: str, context: str = '') -> dict:
    """从纠正中提取模式
    
    返回:
      {'type': '语言风格'|'格式规范'|'内容质量', 'pattern': '规则描述', 'keywords': ['关键词']}
    """
    correction = correction.lower()
    
    # 语言风格类
    if any(kw in correction for kw in ['中英文混用', '不要中文', '英文太多', '纯中文']):
        return {
            'type': '语言风格',
            'pattern': '优先使用纯中文回复，避免中英文混用',
            'keywords': ['中文', '英文', '混用', '语言'],
            'severity': 'medium'
        }
    
    # 格式规范类
    if any(kw in correction for kw in ['分页', '行间距', '字体', '排版', '格式']):
        return {
            'type': '格式规范',
            'pattern': '文档任务需检查分页、行间距、字体等排版细节',
            'keywords': ['分页', '行间距', '字体', '排版', '格式'],
            'severity': 'medium'
        }
    
    # 内容质量类
    if any(kw in correction for kw in ['太简单', '不够深入', '详细一点', '展开讲讲']):
        return {
            'type': '内容质量',
            'pattern': '技术解释需深入底层原理，避免表面描述',
            'keywords': ['深入', '详细', '原理', '底层'],
            'severity': 'high'
        }
    
    # 准确性类
    if any(kw in correction for kw in ['错了', '不对', '应该是', '更正']):
        return {
            'type': '准确性',
            'pattern': f'用户纠正: {correction[:50]}',
            'keywords': ['纠正', '错误', '更正'],
            'severity': 'high'
        }
    
    # 默认
    return {
        'type': '通用',
        'pattern': correction[:100],
        'keywords': [],
        'severity': 'low'
    }


def record_instinct(correction: str, context: str = '') -> dict:
    """记录一个新本能"""
    ensure_dirs()
    index = load_index()
    
    # 提取模式
    pattern_info = extract_pattern(correction, context)
    pattern_text = pattern_info['pattern']
    instinct_id = generate_id(pattern_text)
    
    # 检查是否已存在
    existing = None
    for inst in index['instincts']:
        if inst['id'] == instinct_id:
            existing = inst
            break
    
    if existing:
        # 更新现有本能
        existing['count'] += 1
        existing['last_seen'] = datetime.now().isoformat()
        existing['contexts'].append(context[:200])
        existing['confidence'] = min(existing['count'] / EVOLVE_THRESHOLD, 1.0)
        
        # 保存更新
        save_index(index)
        
        return {
            'action': 'updated',
            'id': instinct_id,
            'pattern': pattern_text,
            'count': existing['count'],
            'confidence': existing['confidence'],
            'can_evolve': existing['count'] >= EVOLVE_THRESHOLD
        }
    
    # 创建新本能
    instinct = {
        'id': instinct_id,
        'type': pattern_info['type'],
        'pattern': pattern_text,
        'keywords': pattern_info['keywords'],
        'severity': pattern_info['severity'],
        'count': 1,
        'confidence': 1.0 / EVOLVE_THRESHOLD,
        'created_at': datetime.now().isoformat(),
        'last_seen': datetime.now().isoformat(),
        'contexts': [context[:200]] if context else [],
        'evolved': False
    }
    
    index['instincts'].append(instinct)
    index['next_id'] += 1
    save_index(index)
    
    # 保存原始记录
    raw_file = f'{RAW_DIR}/{instinct_id}.json'
    with open(raw_file, 'w') as f:
        json.dump(instinct, f, indent=2, ensure_ascii=False)
    
    return {
        'action': 'created',
        'id': instinct_id,
        'pattern': pattern_text,
        'count': 1,
        'confidence': instinct['confidence'],
        'can_evolve': False
    }


def evolve_instincts() -> list:
    """将高置信度本能升级为 SKILL.md 规则片段"""
    ensure_dirs()
    index = load_index()
    evolved = []
    
    for instinct in index['instincts']:
        if instinct['count'] >= EVOLVE_THRESHOLD and not instinct['evolved']:
            # 生成为 SKILL.md 规则片段
            rule = f"""<!-- 本能进化: {instinct['id']} -->
- **类型**: {instinct['type']}
- **规则**: {instinct['pattern']}
- **触发**: 涉及 {', '.join(instinct['keywords'])}
- **置信度**: {instinct['confidence']:.0%} (基于 {instinct['count']} 次纠正)
- **来源**: 用户纠正记录
"""
            
            # 保存 evolved 规则
            evolved_file = f"{EVOLVED_DIR}/{instinct['id']}.md"
            with open(evolved_file, 'w') as f:
                f.write(rule)
            
            instinct['evolved'] = True
            instinct['evolved_at'] = datetime.now().isoformat()
            
            evolved.append({
                'id': instinct['id'],
                'pattern': instinct['pattern'],
                'file': evolved_file
            })
    
    if evolved:
        save_index(index)
    
    return evolved


def get_status() -> dict:
    """获取本能系统状态"""
    index = load_index()
    
    total = len(index['instincts'])
    evolved_count = sum(1 for i in index['instincts'] if i['evolved'])
    ready_to_evolve = sum(1 for i in index['instincts'] 
                         if i['count'] >= EVOLVE_THRESHOLD and not i['evolved'])
    
    # 按类型分组
    by_type = defaultdict(int)
    for i in index['instincts']:
        by_type[i['type']] += 1
    
    return {
        'total_instincts': total,
        'evolved': evolved_count,
        'ready_to_evolve': ready_to_evolve,
        'by_type': dict(by_type),
        'threshold': EVOLVE_THRESHOLD
    }


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='Instinct 系统')
    parser.add_argument('--record', '-r', help='记录一个纠正')
    parser.add_argument('--context', '-c', default='', help='纠正的上下文')
    parser.add_argument('--evolve', '-e', action='store_true', help='进化高置信度本能')
    parser.add_argument('--status', '-s', action='store_true', help='查看状态')
    args = parser.parse_args()
    
    if args.record:
        result = record_instinct(args.record, args.context)
        print(f"{'📝' if result['action'] == 'created' else '🔄'} 本能 {result['action']}")
        print(f"   模式: {result['pattern']}")
        print(f"   次数: {result['count']}")
        print(f"   置信度: {result['confidence']:.0%}")
        if result['can_evolve']:
            print(f"   ✅ 可进化！运行 --evolve 升级")
    
    elif args.evolve:
        evolved = evolve_instincts()
        if evolved:
            print(f"🚀 已进化 {len(evolved)} 个本能为规则:")
            for e in evolved:
                print(f"   - {e['pattern'][:60]}...")
        else:
            print("⏭️  没有可进化的本能（需要置信度 ≥3）")
    
    elif args.status:
        status = get_status()
        print(f"\n{'='*50}")
        print("Instinct 系统状态")
        print(f"{'='*50}")
        print(f"总本能数: {status['total_instincts']}")
        print(f"已进化: {status['evolved']}")
        print(f"待进化: {status['ready_to_evolve']}")
        print(f"进化阈值: {status['threshold']} 次")
        print(f"\n按类型分布:")
        for t, count in status['by_type'].items():
            print(f"  - {t}: {count}")
        print(f"{'='*50}\n")
    
    else:
        parser.print_help()


if __name__ == '__main__':
    main()
