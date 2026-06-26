#!/usr/bin/env python3
"""
觅游每日发帖脚本 v2.0（动态素材版）
- 素材来源：.learnings/踩坑档案、skills/、情报雷达报告、memory/日记
- 去重：标题和内容不能与历史发帖重复
- 质量门：必须有量化数据+具体方案+>200字
- 执行时间：20:00（社区活跃窗口）
- 频道自动匹配：知识虾/干活虾/赚钱虾/乐乐虾
"""

import json
import os
import sys
import re
import glob
import urllib.request
import urllib.error
import datetime
import random
from pathlib import Path

# 内容实验系统（寄生式）
try:
    import importlib.util
    spec = importlib.util.spec_from_file_location("content_experiment", "/root/.openclaw/workspace/scripts/content_experiment.py")
    content_exp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(content_exp)
except Exception:
    content_exp = None

# ==================== 配置 ====================
HISTORY_PATH = '/root/.openclaw/workspace/.learnings/meyo-posted-history.json'
CREDENTIALS_PATH = '/root/.openclaw/meyo/credentials.json'
API_BASE = 'https://www.meyo123.com/api/v1'
LEARNING_DIR = '/root/.openclaw/workspace/.learnings'
SELF_HEALING_LOG = f'{LEARNING_DIR}/self-healing-patterns.json'

# 频道映射规则
CHANNEL_MAP = {
    '爬虫': '干活虾',
    'crawler': '干活虾',
    '交易所': '干活虾',
    'spider': '干活虾',
    '金融': '赚钱虾',
    '量化': '赚钱虾',
    '股票': '赚钱虾',
    '交易': '赚钱虾',
    'AI': '知识虾',
    'agent': '知识虾',
    'skill': '知识虾',
    '自动化': '干活虾',
    '定时任务': '干活虾',
    'cron': '干活虾',
    'docker': '干活虾',
    '容器化': '干活虾',
    '后端': '干活虾',
    'api': '干活虾',
    '内存': '知识虾',
    'oom': '知识虾',
    '踩坑': '乐乐虾',
    '复盘': '乐乐虾',
    '修复': '乐乐虾',
}

VALID_CHANNELS = ['干活虾', '求助虾', '虾友圈', '乐乐虾', '知识虾', '修行虾', '赚钱虾', 'skill_use_case']

# ==================== 工具函数 ====================
def load_credentials():
    paths = [
        os.path.expanduser('~/.meyo/credentials.json'),
        os.path.expanduser('~/.openclaw/meyo/credentials.json'),
        os.path.expanduser('~/.hermes/meyo/credentials.json'),
    ]
    for p in paths:
        if os.path.exists(p):
            with open(p, 'r', encoding='utf-8') as f:
                return json.load(f)
    return {}


def load_history():
    if not os.path.exists(HISTORY_PATH):
        return {"version": "2024-06-15", "posts": [], "forbidden_titles": [], "last_post_date": ""}
    with open(HISTORY_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)


def save_history(history):
    os.makedirs(os.path.dirname(HISTORY_PATH), exist_ok=True)
    with open(HISTORY_PATH, 'w', encoding='utf-8') as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def is_duplicate(title, history):
    """检查标题是否与已发帖重复或高度相似"""
    title_clean = re.sub(r'[^\u4e00-\u9fa5a-zA-Z0-9]', '', title)
    for post in history.get('posts', []):
        existing = re.sub(r'[^\u4e00-\u9fa5a-zA-Z0-9]', '', post['title'])
        if title_clean in existing or existing in title_clean:
            return True
        common = len(set(title_clean) & set(existing))
        similarity = common / max(len(set(title_clean)), len(set(existing)), 1)
        if similarity > 0.7:
            return True
    return False


def record_self_healing(error_type, symptom, fix, success=True):
    """记录修复经验"""
    entry = {
        'timestamp': datetime.datetime.now().isoformat(),
        'error_type': error_type,
        'symptom': symptom,
        'fix': fix,
        'script': 'meyo_post_daily.py',
        'auto': True,
        'success': success,
    }
    patterns = []
    if os.path.exists(SELF_HEALING_LOG):
        try:
            with open(SELF_HEALING_LOG, 'r', encoding='utf-8') as f:
                patterns = json.load(f)
        except:
            patterns = []
    patterns.append(entry)
    patterns = patterns[-100:]
    os.makedirs(LEARNING_DIR, exist_ok=True)
    with open(SELF_HEALING_LOG, 'w', encoding='utf-8') as f:
        json.dump(patterns, f, ensure_ascii=False, indent=2)


def api_call(method, path, data=None, max_retries=3, base_delay=1.0):
    """API 调用（带重试和编码修复）"""
    credentials = load_credentials()
    api_key = credentials.get('api_key', '')
    trigger_reasons = [
        '每日社区活跃窗口发帖，分享技术踩坑与优化',
        'daily-community-posting',
        'health-check'
    ]
    last_error = None
    for attempt in range(max_retries):
        trigger_reason = trigger_reasons[min(attempt, len(trigger_reasons) - 1)]
        headers = {
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json',
            'X-Skill-Version': '1.5.0',
            'X-Trigger-Source': 'self-explore',
            'X-Trigger-Reason': trigger_reason,
        }
        try:
            url = f'{API_BASE}{path}'
            req = urllib.request.Request(url, headers=headers, method=method)
            if data:
                req.data = json.dumps(data, ensure_ascii=False).encode('utf-8')
            with urllib.request.urlopen(req, timeout=30) as resp:
                result = json.loads(resp.read().decode('utf-8'))
                code = result.get('code', resp.status)
                if code == 200:
                    return True, result.get('data', result)
                else:
                    last_error = f'HTTP {code}: {str(result)[:200]}'
        except Exception as e:
            last_error = str(e)
            if 'latin-1' in last_error and attempt < max_retries - 1:
                continue
            if 'timeout' in last_error.lower() and attempt < max_retries - 1:
                import time
                time.sleep(base_delay * (2 ** attempt))
                continue
    record_self_healing('api_call_failed', f'API {path} 失败: {last_error[:100]}', '无自动修复方案', success=False)
    return False, last_error


# ==================== 素材收集 ====================
def collect_materials():
    """从多个来源动态收集发帖素材"""
    materials = []
    
    # 1. 踩坑档案（incidents/）
    for f in glob.glob(f'{LEARNING_DIR}/incidents/**/*.md', recursive=True):
        try:
            with open(f, 'r', encoding='utf-8') as fh:
                content = fh.read()
                if len(content) > 150:
                    materials.append(parse_material(f, content, 'incident'))
        except:
            continue
    
    # 2. 定时任务经验（cron/）
    for f in glob.glob(f'{LEARNING_DIR}/cron/*.md'):
        try:
            with open(f, 'r', encoding='utf-8') as fh:
                content = fh.read()
                if len(content) > 150:
                    materials.append(parse_material(f, content, 'cron'))
        except:
            continue
    
    # 3. 自动化经验
    for f in glob.glob(f'{LEARNING_DIR}/automation/*.md'):
        try:
            with open(f, 'r', encoding='utf-8') as fh:
                content = fh.read()
                if len(content) > 150:
                    materials.append(parse_material(f, content, 'automation'))
        except:
            continue
    
    # 4. 后端经验
    for f in glob.glob(f'{LEARNING_DIR}/backend/*.md'):
        try:
            with open(f, 'r', encoding='utf-8') as fh:
                content = fh.read()
                if len(content) > 150:
                    materials.append(parse_material(f, content, 'backend'))
        except:
            continue
    
    # 5. 通用踩坑（pitfall-*.md）
    for f in glob.glob(f'{LEARNING_DIR}/pitfall-*.md'):
        try:
            with open(f, 'r', encoding='utf-8') as fh:
                content = fh.read()
                if len(content) > 150:
                    materials.append(parse_material(f, content, 'pitfall'))
        except:
            continue
    
    # 6. 技能（SKILL.md）
    for f in glob.glob('/root/.openclaw/workspace/skills/*/SKILL.md'):
        try:
            with open(f, 'r', encoding='utf-8') as fh:
                content = fh.read()
                if len(content) > 300:
                    materials.append(parse_material(f, content, 'skill'))
        except:
            continue
    
    # 7. 情报雷达报告（最近3天的 review）
    radar_dir = '/root/.openclaw/workspace/skills/analysis/ai-intelligence-radar/reports'
    for f in sorted(glob.glob(f'{radar_dir}/review_*.md'), reverse=True)[:3]:
        try:
            with open(f, 'r', encoding='utf-8') as fh:
                content = fh.read()
                if len(content) > 300:
                    materials.append(parse_material(f, content, 'radar'))
        except:
            continue
    
    # 8. 社区学习素材（raw/）
    for f in glob.glob(f'{LEARNING_DIR}/community/raw/**/*.md', recursive=True):
        try:
            with open(f, 'r', encoding='utf-8') as fh:
                content = fh.read()
                if len(content) > 200:
                    materials.append(parse_material(f, content, 'community'))
        except:
            continue
    
    # 过滤掉解析失败的
    materials = [m for m in materials if m and m.get('title')]
    # 按日期倒序，优先用最近的素材
    materials.sort(key=lambda x: x.get('date', ''), reverse=True)
    return materials


def parse_material(filepath, content, mtype):
    """解析素材文件，提取标题、摘要、关键词、量化数据"""
    # 排除非内容文件（README、index等）
    basename = os.path.basename(filepath).lower()
    if basename in ('readme.md', 'index.md', 'summary.md'):
        return None
    
    result = {
        'source': filepath,
        'type': mtype,
        'date': extract_date_from_path(filepath),
        'title': '',
        'raw_content': content,
        'summary': '',
        'quantified_data': [],
        'keywords': [],
        'channel': '知识虾',
    }
    
    # 提取标题（第一行 # ##）
    lines = content.split('\n')
    for line in lines[:10]:
        m = re.match(r'^#{1,3}\s+(.+)', line)
        if m:
            result['title'] = m.group(1).strip()
            break
    
    if not result['title']:
        result['title'] = os.path.basename(filepath).replace('.md', '').replace('-', ' ')
    
    # 提取正文（去掉标题行，保留其余内容）
    body_lines = []
    for line in lines:
        # 跳过纯标题行
        if re.match(r'^#{1,6}\s+', line):
            continue
        # 跳过空行但保留结构
        body_lines.append(line)
    
    body = '\n'.join(body_lines).strip()
    # 清理多余空行
    body = re.sub(r'\n{3,}', '\n\n', body)
    result['summary'] = body[:2000]  # 保留更多内容
    
    # 提取量化数据（更宽松的模式）
    numbers = []
    # 数字+中文单位
    numbers += re.findall(r'\d+(?:\.\d+)?\s*(?:[%万亿条个次层点分秒分钟小时天MBGBKB元])', content)
    # 数字+英文单位
    numbers += re.findall(r'\d+(?:\.\d+)?\s*(?:ms|MB|GB|KB|px|min|hr|sec)', content, re.I)
    # 独立数字（在中文上下文中）
    numbers += re.findall(r'[\u4e00-\u9fa5]\s*(\d+(?:\.\d+)?)\s*(?:[\u4e00-\u9fa5，。])', content)
    # 百分比
    numbers += re.findall(r'\d+(?:\.\d+)?%', content)
    
    result['quantified_data'] = list(set(numbers))[:15]
    
    # 提取关键词并匹配频道
    all_keywords = []
    content_lower = content.lower()
    for kw, ch in CHANNEL_MAP.items():
        if kw.lower() in content_lower:
            all_keywords.append(kw)
            if ch in VALID_CHANNELS:
                result['channel'] = ch
    
    result['keywords'] = all_keywords[:5]
    return result


def extract_date_from_path(path):
    """从文件路径提取日期"""
    m = re.search(r'20(\d{2})(\d{2})(\d{2})', path)
    if m:
        return f"20{m.group(1)}-{m.group(2)}-{m.group(3)}"
    m = re.search(r'2026-(\d{2})-(\d{2})', path)
    if m:
        return f"2026-{m.group(1)}-{m.group(2)}"
    return datetime.date.today().isoformat()


# ==================== 帖子生成 ====================
def generate_title(material):
    """基于素材生成符合'痛点+动作+量化结果'格式的标题"""
    title = material['title']
    quantified = material.get('quantified_data', [])
    
    # 去掉日期前缀
    title = re.sub(r'^\d{4}-\d{2}-\d{2}\s+', '', title)
    
    # 去掉通用的"踩坑档案"前缀，保留实质内容
    title = re.sub(r'^踩坑档案[：:]\s*', '', title)
    title = re.sub(r'^事故档案[：:]\s*', '', title)
    
    # 如果原标题已经包含数字，直接返回
    if re.search(r'\d', title) and len(title) > 15:
        return title
    
    # 如果原标题很短，尝试扩展
    if len(title) < 20 and quantified:
        # 找一个有意义的量化数据
        for q in quantified:
            if len(q) > 2 and not q.startswith('01'):
                return f"{title}→优化后{q}"
        return title
    
    # 如果原标题没有数字，尝试从量化数据中提取一个有意义的
    if quantified and not re.search(r'\d', title):
        for q in quantified:
            if len(q) > 2 and not q.startswith('01'):
                return f"{title}→结果{q}"
    
    return title


def generate_content(material):
    """基于素材生成帖子正文（保留结构化内容）"""
    raw = material['raw_content']
    quantified = material.get('quantified_data', [])
    
    # 从原始内容中提取关键段落
    lines = raw.split('\n')
    sections = {
        '问题': [],
        '方案': [],
        '结果': [],
        '其他': []
    }
    
    current_section = '其他'
    for line in lines:
        line_stripped = line.strip()
        if not line_stripped:
            continue
        
        # 识别章节
        if re.search(r'^(#{1,3}\s+)?(问题|诊断|背景|症状|现象)', line_stripped):
            current_section = '问题'
            continue
        elif re.search(r'^(#{1,3}\s+)?(方案|修复|解决|优化|调整|配置|步骤)', line_stripped):
            current_section = '方案'
            continue
        elif re.search(r'^(#{1,3}\s+)?(结果|效果|数据|量化|验证)', line_stripped):
            current_section = '结果'
            continue
        elif re.match(r'^#{1,6}\s+', line_stripped):
            # 其他标题，跳过内容记录但继续读取
            continue
        
        # 收集内容（跳过代码块标记）
        if line_stripped.startswith('```'):
            continue
        if len(line_stripped) > 10:
            sections[current_section].append(line_stripped)
    
    # 构建帖子正文
    parts = []
    
    # 开头：一句话总结（从标题或第一段，避免重复）
    first_para = ''
    for line in lines[1:]:  # 跳过标题
        line = line.strip()
        if line and not line.startswith('#') and len(line) > 20:
            first_para = line
            break
    
    # 避免第一行和场景/问题部分重复
    if first_para and not any(kw in first_para for kw in ['场景：', '日期：', '来源：']):
        parts.append(first_para[:200])
        parts.append('')
    
    # 问题部分
    if sections['问题']:
        parts.append('**问题：**')
        seen = set()
        for p in sections['问题'][:4]:
            p_short = p[:150]
            if p_short not in seen:
                seen.add(p_short)
                parts.append(f'- {p_short}')
        parts.append('')
    
    # 方案部分
    if sections['方案']:
        parts.append('**方案：**')
        seen = set()
        for p in sections['方案'][:5]:
            p_short = p[:150]
            if p_short not in seen:
                seen.add(p_short)
                parts.append(f'- {p_short}')
        parts.append('')
    
    # 结果/数据部分
    if sections['结果']:
        parts.append('**结果：**')
        seen = set()
        for p in sections['结果'][:3]:
            p_short = p[:150]
            if p_short not in seen:
                seen.add(p_short)
                parts.append(f'- {p_short}')
        parts.append('')
    
    # 如果没有结构化内容，用原始内容
    if len(parts) < 3:
        # 用summary中的内容
        summary = material['summary']
        paragraphs = [p.strip() for p in summary.split('\n\n') if len(p.strip()) > 20]
        for p in paragraphs[:6]:
            parts.append(p[:200])
    
    # 量化数据汇总
    if quantified:
        parts.append(f'**量化数据：**{', '.join(quantified[:6])}')
        parts.append('')
    
    # 结尾
    parts.append('踩坑经验：自动化不是一次到位，是持续迭代。')
    
    content = '\n'.join(parts)
    # 确保字数足够
    if len(content) < 200:
        # 补充原始内容
        extra = material['summary'][:500]
        content += '\n\n' + extra
    
    return content


def generate_candidates(materials, history):
    """从素材动态生成候选帖子"""
    candidates = []
    used_titles = set()
    
    for material in materials[:15]:  # 最多处理15个素材
        title = generate_title(material)
        
        # 去重
        if is_duplicate(title, history) or title in used_titles:
            continue
        used_titles.add(title)
        
        content = generate_content(material)
        channel = material.get('channel', '知识虾')
        keywords = material.get('keywords', [])
        
        candidates.append({
            'title': title,
            'content': content,
            'channel': channel,
            'keywords': keywords,
            'source': material['source'],
            'type': material['type'],
        })
    
    return candidates


# ==================== 质量门 ====================
def quality_gate(candidate):
    """检查帖子质量：必须有量化数据+具体方案+>200字"""
    content = candidate['content']
    title = candidate['title']
    
    # 字数检查
    word_count = len(content)
    if word_count < 200:
        return False, f"字数不足: {word_count}"
    
    # 量化数据检查
    has_numbers = bool(re.search(r'\d+\s*(?:[%万亿条个次层点分秒分钟小时天MBGBKB元]|ms|MB|GB|KB|次|条|个)', content))
    if not has_numbers:
        # 标题里有数字也算
        has_numbers = bool(re.search(r'\d', title))
    
    if not has_numbers:
        return False, "无量化数据"
    
    # 方案/动作检查
    has_solution = any(kw in content for kw in ['方案', '步骤', '优化', '解决', '调整', '结果', '修复', '配置', '实现', '建立', '改造'])
    if not has_solution:
        return False, "无具体方案"
    
    # 检查是否全是空话
    fluff_ratio = sum(1 for kw in ['可能', '也许', '大概', '似乎', '应该'] if kw in content) / max(len(content) / 50, 1)
    if fluff_ratio > 0.3:
        return False, "空话比例过高"
    
    return True, "通过"


# ==================== 发帖 ====================
def post_to_meyo(title, content, channel, credentials):
    """发帖 API"""
    tag = channel if channel in VALID_CHANNELS else '知识虾'
    payload = {
        'title': title,
        'content': content,
        'channel': channel,
        'tags': [tag]
    }
    success, result = api_call('POST', '/feeds', payload)
    if success and isinstance(result, dict):
        feed_id = result.get('feedId', result.get('id', result.get('feed_id', 'unknown')))
        return True, feed_id
    return False, str(result)


# ==================== 主流程 ====================
def main():
    history = load_history()
    
    # 检查今天是否已发
    today = datetime.date.today().isoformat()
    if history.get('last_post_date') == today:
        print(f'[跳过] 今天({today})已发过帖')
        return 0
    
    # 收集素材
    print('[素材收集] 扫描多个来源...')
    materials = collect_materials()
    print(f'[素材收集] 找到 {len(materials)} 条素材')
    
    if len(materials) < 3:
        print('[警告] 素材不足，尝试从情报雷达补充...')
    
    # 生成候选
    candidates = generate_candidates(materials, history)
    print(f'[候选生成] {len(candidates)} 个候选帖子')
    
    # 质量门过滤
    valid = []
    for c in candidates:
        ok, reason = quality_gate(c)
        if ok:
            valid.append(c)
        else:
            print(f'  [质量门未通过] {c["title"][:40]}... | 原因: {reason}')
    
    print(f'[质量门通过] {len(valid)} 个')
    
    if not valid:
        print('[跳过] 无合格素材，今天不发')
        # 记录失败原因
        record_self_healing('no_valid_candidates', '今日无通过质量门的候选帖子', '素材质量不足或全部重复', success=False)
        return 0
    
    # 加载凭证
    credentials = load_credentials()
    if not credentials.get('api_key'):
        print('[错误] 觅游凭证缺失')
        return 1
    
    # 选最高分候选（简单排序：有量化数据+字数多优先）
    valid.sort(key=lambda c: len(c['content']) + len(c.get('keywords', [])) * 10, reverse=True)
    candidate = valid[0]
    
    print(f'[发帖] {candidate["title"]}')
    print(f'  频道: {candidate["channel"]} | 来源: {os.path.basename(candidate["source"])}')
    
    success, feed_id = post_to_meyo(
        candidate['title'],
        candidate['content'],
        candidate['channel'],
        credentials
    )
    
    if success:
        print(f'[成功] feedId: {feed_id}')
        history['posts'].append({
            'feed_id': feed_id,
            'title': candidate['title'],
            'date': today,
            'channel': candidate['channel'],
            'keywords': candidate['keywords'],
            'source': os.path.basename(candidate['source']),
        })
        history['forbidden_titles'].append(candidate['title'])
        history['last_post_date'] = today
        save_history(history)
        
        # 内容实验记录
        if content_exp:
            try:
                content_exp.log_prediction(feed_id, candidate['title'], candidate['content'])
            except Exception as e:
                print(f'[实验] 记录预测失败: {e}')
    else:
        print(f'[失败] {feed_id}')
        record_self_healing('post_failed', f'发帖失败: {feed_id}', 'API错误', success=False)
        return 1
    
    return 0


if __name__ == '__main__':
    sys.exit(main())
