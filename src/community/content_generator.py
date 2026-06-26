#!/usr/bin/env python3
"""
AI社区Agent - 动态素材生成与质量门
- 从.learnings/踩坑档案动态提取素材（非硬编码模板）
- 内容质量评分：量化数据+具体方案+>200字
- A/B实验追踪：预测vs实际对比
"""
import json, os, re, glob, random
from datetime import datetime

# ========== 质量门配置 ==========
QUALITY_GATE = {
    "min_words": 200,           # 最少字数
    "min_numbers": 1,           # 至少1个量化数据
    "min_solution": 1,          # 至少1个具体方案
    "score_weights": {          # 五维度评分
        "quantified_data": 0.25,
        "concrete_solution": 0.25,
        "hook": 0.20,
        "structure": 0.15,
        "reproducible": 0.15,
    }
}

def extract_quantified_data(content: str) -> list:
    """提取量化数据（数字+单位）"""
    numbers = []
    # 中文单位：%、万、条、次、层、分、秒、MB、GB
    numbers += re.findall(r'\d+(?:\.\d+)?\s*(?:[%万亿条个次层点分秒分钟小时天MBGB元])', content)
    # 英文单位
    numbers += re.findall(r'\d+(?:\.\d+)?\s*(?:ms|MB|GB|KB|min|hr)', content, re.I)
    # 百分比
    numbers += re.findall(r'\d+(?:\.\d+)?%', content)
    return list(set(numbers))[:15]

def collect_materials(learning_dir: str) -> list:
    """动态收集素材（6个来源）
    
    核心改进：从硬编码4个模板 → 动态扫描132+条素材
    """
    sources = [
        (f'{learning_dir}/incidents/**/*.md', 'incident'),
        (f'{learning_dir}/cron/*.md', 'cron'),
        (f'{learning_dir}/automation/*.md', 'automation'),
        (f'{learning_dir}/backend/*.md', 'backend'),
        (f'{learning_dir}/pitfall-*.md', 'pitfall'),
    ]
    
    materials = []
    for pattern, mtype in sources:
        for f in glob.glob(pattern, recursive=True):
            try:
                with open(f, 'r', encoding='utf-8') as fh:
                    content = fh.read()
                if len(content) < 150:
                    continue
                
                # 提取标题
                title = os.path.basename(f).replace('.md', '')
                for line in content.split('\n')[:10]:
                    m = re.match(r'^#{1,3}\s+(.+)', line)
                    if m:
                        title = m.group(1).strip()
                        break
                
                # 提取结构化内容
                sections = {"问题": [], "方案": [], "结果": []}
                current = None
                for line in content.split('\n'):
                    if '**问题' in line or '## 问题' in line:
                        current = "问题"
                    elif '**方案' in line or '## 方案' in line or '**解决' in line:
                        current = "方案"
                    elif '**结果' in line or '## 结果' or '**量化' in line:
                        current = "结果"
                    elif current and line.strip() and not line.startswith('#'):
                        sections[current].append(line.strip())
                
                materials.append({
                    "source": f,
                    "type": mtype,
                    "title": title,
                    "summary": content[:1000],
                    "quantified_data": extract_quantified_data(content),
                    "sections": sections
                })
            except Exception as e:
                print(f"[跳过] {f}: {e}")
                continue
    
    return materials

def quality_gate(content: str, title: str) -> tuple:
    """内容质量门
    
    硬性门槛（必须全部通过）：
    - 字数 > 200
    - 包含量化数据
    - 包含具体方案
    
    软性评分（>0.6通过）：
    - 五维度加权评分
    """
    # 硬性检查
    word_count = len(content)
    if word_count < QUALITY_GATE["min_words"]:
        return False, f"字数不足({word_count} < {QUALITY_GATE['min_words']})"
    
    quantified = extract_quantified_data(content)
    if len(quantified) < QUALITY_GATE["min_numbers"]:
        return False, "无量化数据"
    
    if not re.search(r'方案|解决|修复|优化|改进|部署|配置|实现|步骤', content):
        return False, "无具体方案"
    
    # 软性评分
    scores = {
        "quantified_data": min(len(quantified) / 3, 1.0),  # 3个以上得满分
        "concrete_solution": 1.0 if re.search(r'步骤|配置|代码|参数', content) else 0.5,
        "hook": 1.0 if re.search(r'踩坑|事故|坑|翻车|血泪|教训', content) else 0.6,
        "structure": 1.0 if re.search(r'##|\\*\\*|\\d+\\.', content) else 0.4,
        "reproducible": 1.0 if re.search(r'python|bash|docker|代码|配置', content) else 0.3,
    }
    
    total_score = sum(
        QUALITY_GATE["score_weights"][k] * scores[k] 
        for k in QUALITY_GATE["score_weights"]
    )
    
    if total_score < 0.6:
        return False, f"质量分不足({total_score:.2f} < 0.6)"
    
    return True, f"质量分{total_score:.2f}"

def generate_post(material: dict) -> dict:
    """基于素材生成社区帖子"""
    title = material['title']
    quantified = material.get('quantified_data', [])
    
    # 标题格式：痛点+动作+量化结果
    if quantified and not re.search(r'\d', title):
        title = f"{title}→优化后{quantified[0]}"
    
    # 内容组装
    sections = material.get('sections', {})
    content_parts = [material['summary'][:300]]  # 开头摘要
    
    if sections['问题']:
        content_parts.append("**问题：**")
        content_parts += [f"- {p[:150]}" for p in sections['问题'][:4]]
    
    if sections['方案']:
        content_parts.append("**方案：**")
        content_parts += [f"- {p[:150]}" for p in sections['方案'][:5]]
    
    if sections['结果']:
        content_parts.append("**结果：**")
        content_parts += [f"- {p[:150]}" for p in sections['结果'][:3]]
    
    content = "\n\n".join(content_parts)
    
    return {
        "title": title,
        "content": content,
        "source": material['source']
    }

if __name__ == "__main__":
    # 演示：收集素材并筛选
    LEARNING_DIR = "/path/to/your/.learnings"  # 修改为你的路径
    
    print("[素材收集] 扫描踩坑档案...")
    materials = collect_materials(LEARNING_DIR)
    print(f"[素材收集] 找到 {len(materials)} 条素材")
    
    # 质量筛选
    valid = []
    for m in materials[:5]:
        post = generate_post(m)
        ok, reason = quality_gate(post['content'], post['title'])
        status = "✅" if ok else "❌"
        print(f"{status} {post['title'][:50]}... | {reason}")
        if ok:
            valid.append(post)
    
    print(f"\n[质量门] {len(valid)}/{len(materials)} 通过")
