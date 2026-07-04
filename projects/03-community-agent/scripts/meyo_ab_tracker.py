#!/usr/bin/env python3
"""
meyo_ab_tracker.py - 觅游 A/B 标题对比实验追踪器
核心：同一素材生成2-3个标题变体，早晚分别发布，追踪实际表现
数据：一张 JSON，记录变体→表现→沉淀公式
"""
import json
import os
import urllib.request
import urllib.error
import datetime
import re
from pathlib import Path

DATA_PATH = '/root/.openclaw/workspace/.learnings/meyo-ab-experiments.json'
CREDENTIALS_PATH = '/root/.openclaw/meyo/credentials.json'
API_BASE = 'https://www.meyo123.com/api/v1'

# 标题变体模板
TITLE_VARIANTS = {
    "pain_action": {
        "name": "痛点+动作",
        "template": "{pain}→{action}，{result}",
        "example": "定时任务断线42天没人发现→三层巡检方案，空转误报从12%降到0.8%"
    },
    "number_shock": {
        "name": "数字冲击",
        "template": "{number}{unit}{outcome}：{solution}",
        "example": "16次无效采集到精准触发：一个timeout参数的血泪教训"
    },
    "counter_intuitive": {
        "name": "反直觉",
        "template": "{common_belief}？{truth}",
        "example": "以为加了熔断就稳了？直到碰到连续503不抛异常"
    }
}


def load_data():
    """加载实验数据"""
    if os.path.exists(DATA_PATH):
        with open(DATA_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {
        "version": 1,
        "formula_history": [],
        "experiments": [],
        "winning_patterns": {}
    }


def save_data(data):
    Path(DATA_PATH).parent.mkdir(parents=True, exist_ok=True)
    with open(DATA_PATH, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_credentials():
    paths = [
        os.path.expanduser('~/.meyo/credentials.json'),
        os.path.expanduser('~/.openclaw/meyo/credentials.json'),
    ]
    for p in paths:
        if os.path.exists(p):
            with open(p, 'r', encoding='utf-8') as f:
                return json.load(f)
    return {}


def api_call(method, path, data=None):
    """轻量 API 调用"""
    creds = load_credentials()
    headers = {
        'Authorization': f'Bearer {creds.get("api_key", "")}',
        'Content-Type': 'application/json',
        'X-Skill-Version': '1.5.0',
        'X-Trigger-Source': 'self-explore',
        'X-Trigger-Reason': 'ab-experiment-track'
    }
    url = f'{API_BASE}{path}'
    body = json.dumps(data, ensure_ascii=False).encode('utf-8') if data else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        return e.code, {}
    except Exception as e:
        return 0, str(e)


def generate_title_variants(base_title, content):
    """
    基于素材生成3个标题变体
    返回: [(variant_type, title), ...]
    """
    variants = []
    
    # 清洗标题：去掉 LEARNED-XXX 编号和日期前缀
    cleaned = base_title
    cleaned = re.sub(r'^LEARNED-\d+[:：]?\s*', '', cleaned)
    cleaned = re.sub(r'^\d{4}-\d{2}-\d{2}\s+', '', cleaned)
    cleaned = re.sub(r'^LEARNED-\d+[:：]?\s*', '', cleaned)  # 再次清理（日期去掉后LEARNED可能还在）
    cleaned = re.sub(r'^(踩坑档案|事故档案|补充|觅游社区)[:：]?\s*', '', cleaned)
    cleaned = re.sub(r'\s*\d{4}批次$', '', cleaned)
    cleaned = re.sub(r'\s*（\d{2}:\d{2}批次）\s*$', '', cleaned)
    cleaned = cleaned.strip()
    
    # 提取关键信息
    numbers = re.findall(r'\d+(?:\.\d+)?[%\s]*[万亿条个次层点分秒分钟小时天MBGBKB元]', content)
    numbers += re.findall(r'\d+(?:\.\d+)?%', content)
    
    # 找痛点关键词
    pain_keywords = ['坑', '陷阱', '失败', '错误', '踩坑', '血泪', '崩溃', '断线', '静默', '超时', '无效', '漏掉']
    pain_found = [kw for kw in pain_keywords if kw in content]
    
    # 变体1: 痛点+动作
    if pain_found:
        pain = pain_found[0]
        # 提取动作
        action_match = re.search(r'(修复|解决|优化|调整|改造|重构|设计|方案|框架|策略|搞定|处理)[^，。]{2,20}', content)
        action = action_match.group(1) if action_match else '优化'
        # 提取结果
        result = numbers[0] if numbers else '问题解决了'
        v1 = f"{pain}了？{action}之后{result}"
        variants.append(("pain_action", v1))
    else:
        # 没有痛点，用问题+方案
        # 从内容中提取一个核心概念
        concept_match = re.search(r'(定时任务|爬虫|API|内存|缓存|数据库|部署|监控|测试|优化|框架|策略|方案)[^，。]{2,20}', content)
        concept = concept_match.group(0) if concept_match else cleaned[:20]
        v1 = f"{concept}→一个unexpected的解决方案"
        variants.append(("pain_action", v1))
    
    # 变体2: 数字冲击（优先用清洗后的标题）
    if numbers:
        num = numbers[0]
        # 如果清洗后的标题有实质内容，用它
        if cleaned and len(cleaned) > 10 and not cleaned.startswith('从'):
            v2 = f"{num}的代价：{cleaned[:30]}"
        else:
            v2 = f"{num}的代价：技术优化实践"
        variants.append(("number_shock", v2))
    else:
        # 从内容中提取核心概念
        if cleaned and len(cleaned) > 10:
            v2 = f"从0到1：{cleaned[:30]}"
        else:
            v2 = "从踩坑到方案：一次技术优化记录"
        variants.append(("number_shock", v2))
    
    # 变体3: 反直觉
    counter_words = ['以为', '没想到', '实际上', '反而', '不是...而是', '但', '却', '然而']
    if any(w in content for w in counter_words):
        # 提取前面部分
        if cleaned and len(cleaned) > 10:
            v3 = f"以为解决了{cleaned[:25]}？直到..."
        else:
            v3 = f"以为优化了？实际还有更多坑"
        variants.append(("counter_intuitive", v3))
    else:
        if cleaned and len(cleaned) > 10:
            v3 = f"{cleaned[:30]}，但代价是..."
        else:
            v3 = "技术优化有收益，但代价是..."
        variants.append(("counter_intuitive", v3))
    
    return variants


def log_experiment(material_id, variants, morning_post_id=None, evening_post_id=None):
    """
    记录一次 A/B 实验
    variants: [(type, title), ...]
    """
    data = load_data()
    
    exp = {
        "experiment_id": f"ab_{datetime.datetime.now().strftime('%Y%m%d')}_{material_id[:8]}",
        "material_id": material_id,
        "date": datetime.date.today().isoformat(),
        "variants": [
            {
                "type": v[0],
                "title": v[1],
                "post_id": morning_post_id if i == 0 else evening_post_id,
                "slot": "morning" if i == 0 else "evening",
                "predicted_likes": 5 if i == 0 else 8,  # evening 通常更高
                "predicted_comments": 2 if i == 0 else 3,
                "actual_likes": None,
                "actual_comments": None,
                "retro_done": False
            }
            for i, v in enumerate(variants[:2])  # 只取前2个变体
        ],
        "created_at": datetime.datetime.now().isoformat()
    }
    
    data["experiments"].append(exp)
    save_data(data)
    return exp


def fetch_post_metrics(post_id):
    """获取帖子实际数据"""
    if not post_id:
        return None
    code, resp = api_call('GET', f'/feeds/{post_id}')
    if code != 200:
        return None
    feed = resp.get('data', resp)
    if not feed:
        return None
    return {
        "likes": feed.get('likeCount', feed.get('likes', 0)),
        "comments": feed.get('commentCount', feed.get('comments', 0)),
        "views": feed.get('viewCount', feed.get('views', 0))
    }


def do_retro(experiment_id=None, dry_run=False):
    """
    执行复盘（指定 experiment_id 或自动找满3天的）
    返回复盘数量
    """
    data = load_data()
    now = datetime.datetime.now()
    retros_done = 0
    
    for exp in data["experiments"]:
        if exp.get("retro_done"):
            continue
        if experiment_id and exp["experiment_id"] != experiment_id:
            continue
        
        # 检查是否满3天
        posted = datetime.datetime.fromisoformat(exp["created_at"])
        if (now - posted).days < 3 and not experiment_id:
            continue
        
        # 拉取每个变体的实际数据
        for variant in exp["variants"]:
            actual = fetch_post_metrics(variant.get("post_id"))
            if actual:
                variant["actual_likes"] = actual["likes"]
                variant["actual_comments"] = actual["comments"]
                variant["actual_views"] = actual.get("views", 0)
        
        # 判定胜负
        morning = exp["variants"][0] if len(exp["variants"]) > 0 else None
        evening = exp["variants"][1] if len(exp["variants"]) > 1 else None
        
        if morning and evening and morning.get("actual_likes") is not None and evening.get("actual_likes") is not None:
            # 计算综合得分（点赞*1 + 评论*2 + 浏览*0.1）
            m_score = morning["actual_likes"] * 1 + morning.get("actual_comments", 0) * 2 + morning.get("actual_views", 0) * 0.1
            e_score = evening["actual_likes"] * 1 + evening.get("actual_comments", 0) * 2 + evening.get("actual_views", 0) * 0.1
            
            if m_score > e_score:
                exp["winner"] = "morning"
                exp["winner_type"] = morning["type"]
            else:
                exp["winner"] = "evening"
                exp["winner_type"] = evening["type"]
            
            exp["winner_margin"] = abs(m_score - e_score)
        
        exp["retro_done"] = True
        exp["retro_at"] = now.isoformat()
        retros_done += 1
        
        print(f'[A/B复盘] {exp["experiment_id"]}: {morning["type"]} vs {evening["type"]}')
        if morning:
            print(f'  早间: {morning.get("actual_likes", "?")}赞 {morning.get("actual_comments", "?")}评')
        if evening:
            print(f'  晚间: {evening.get("actual_likes", "?")}赞 {evening.get("actual_comments", "?")}评')
        if exp.get("winner"):
            print(f'  胜出: {exp["winner"]} ({exp["winner_type"]})')
    
    if retros_done > 0 and not dry_run:
        save_data(data)
        update_winning_patterns()
    
    return retros_done


def update_winning_patterns():
    """更新获胜模式统计"""
    data = load_data()
    done = [e for e in data["experiments"] if e.get("retro_done") and e.get("winner_type")]
    
    if len(done) < 3:
        return
    
    from collections import Counter
    winners = [e["winner"] for e in done]
    winner_types = [e["winner_type"] for e in done]
    
    slot_counter = Counter(winners)
    type_counter = Counter(winner_types)
    
    data["winning_patterns"] = {
        "total_experiments": len(done),
        "slot_win_rate": {
            slot: count / len(done) for slot, count in slot_counter.items()
        },
        "type_win_rate": {
            t: count / len(done) for t, count in type_counter.items()
        },
        "updated_at": datetime.datetime.now().isoformat()
    }
    
    save_data(data)
    
    # 写入可读报告
    report_path = '/root/.openclaw/workspace/.learnings/meyo-ab-report.md'
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(f"# 觅游标题 A/B 实验报告\n\n")
        f.write(f"**更新时间**: {datetime.date.today()}\n\n")
        f.write(f"**总实验数**: {len(done)}\n\n")
        f.write("## 时段胜率\n\n")
        for slot, rate in slot_counter.items():
            f.write(f"- {slot}: {rate/len(done)*100:.1f}% ({rate}/{len(done)})\n")
        f.write("\n## 标题类型胜率\n\n")
        for t, count in type_counter.most_common():
            f.write(f"- {t}: {count/len(done)*100:.1f}% ({count}/{len(done)})\n")
        f.write("\n## 建议\n\n")
        best_slot = slot_counter.most_common(1)[0][0] if slot_counter else "unknown"
        best_type = type_counter.most_common(1)[0][0] if type_counter else "unknown"
        f.write(f"- **最佳时段**: {best_slot}\n")
        f.write(f"- **最佳标题类型**: {best_type}\n")
        f.write(f"- **推荐组合**: 在{best_slot}使用「{best_type}」型标题\n")


def check_pending_retros():
    """检查有多少实验待复盘"""
    data = load_data()
    now = datetime.datetime.now()
    pending = []
    
    for exp in data["experiments"]:
        if exp.get("retro_done"):
            continue
        posted = datetime.datetime.fromisoformat(exp["created_at"])
        days = (now - posted).days
        if days >= 3:
            pending.append({
                "experiment_id": exp["experiment_id"],
                "days_since_post": days
            })
    
    return pending


def get_best_title_type():
    """获取当前最佳标题类型"""
    data = load_data()
    patterns = data.get("winning_patterns", {})
    type_rates = patterns.get("type_win_rate", {})
    if type_rates:
        return max(type_rates, key=type_rates.get)
    return "pain_action"  # 默认


def status():
    """输出 A/B 系统状态"""
    data = load_data()
    total = len(data["experiments"])
    done = len([e for e in data["experiments"] if e.get("retro_done")])
    pending = check_pending_retros()
    patterns = data.get("winning_patterns", {})
    
    print("=" * 50)
    print("📊 觅游标题 A/B 实验系统")
    print("=" * 50)
    print(f"  总实验数: {total}")
    print(f"  已复盘: {done}")
    print(f"  待复盘: {len(pending)}")
    if patterns:
        print(f"  最佳时段: {max(patterns.get('slot_win_rate', {}), key=patterns['slot_win_rate'].get, default='unknown')}")
        print(f"  最佳标题: {max(patterns.get('type_win_rate', {}), key=patterns['type_win_rate'].get, default='unknown')}")
    print("=" * 50)


if __name__ == '__main__':
    import sys
    if len(sys.argv) < 2:
        status()
    elif sys.argv[1] == 'retro':
        exp_id = sys.argv[2] if len(sys.argv) > 2 else None
        do_retro(exp_id)
    elif sys.argv[1] == 'pending':
        pending = check_pending_retros()
        print(f"待复盘: {len(pending)} 条")
        for p in pending:
            print(f"  {p['experiment_id']} ({p['days_since_post']}天前)")
    elif sys.argv[1] == 'best':
        print(get_best_title_type())
