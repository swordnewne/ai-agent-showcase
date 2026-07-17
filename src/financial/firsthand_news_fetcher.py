#!/usr/bin/env python3
"""
第一手重大事件采集器

监控源：
1. 新华社快讯 (xinhuanet.com) - 国家级重大事件
2. 中国政府网 (gov.cn) - 国务院/部委政策
3. 中国人民银行 (pbc.gov.cn) - 货币政策
4. 证监会 (csrc.gov.cn) - 监管政策

与财经媒体的区别：
- 这些源比新浪/WSJ快 1-2 小时
- 但覆盖面更宽（非财经类也报）
- 需要AI判断是否与A股/市场相关

频率：5分钟（与财经新闻并行）
"""

import json
import os
import sys
import urllib.request
import urllib.parse
import re
from datetime import datetime, timezone, timedelta
from typing import List, Dict

WORKSPACE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.path.join(WORKSPACE, "data")
ALERTS_DIR = os.path.join(WORKSPACE, ".alerts")
FIRSTHAND_FILE = os.path.join(DATA_DIR, "firsthand_news.jsonl")

# DeepSeek API
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")

if not DEEPSEEK_API_KEY:
    env_file = os.path.join(WORKSPACE, ".env")
    if os.path.exists(env_file):
        with open(env_file, "r") as f:
            for line in f:
                if line.startswith("DEEPSEEK_API_KEY="):
                    DEEPSEEK_API_KEY = line.strip().split("=", 1)[1]
                elif line.startswith("DEEPSEEK_BASE_URL="):
                    DEEPSEEK_BASE_URL = line.strip().split("=", 1)[1]
                elif line.startswith("DEEPSEEK_MODEL="):
                    DEEPSEEK_MODEL = line.strip().split("=", 1)[1]


def ensure_dirs():
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(ALERTS_DIR, exist_ok=True)


def fetch_xinhua() -> List[Dict]:
    """获取新华社快讯"""
    url = "http://www.news.cn/politics/"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as response:
            html = response.read().decode("utf-8")
            # 提取新闻标题和链接（简化版，用正则）
            news_items = []
            # 匹配新闻标题模式
            pattern = r'<li[^\u003e]*\u003e[^\u003c]*<a[^\u003e]*href="([^"]+)"[^\u003e]*\u003e([^\u003c]+)</a\u003e'
            matches = re.findall(pattern, html)
            for href, title in matches[:20]:
                if len(title.strip()) > 10:
                    news_items.append({
                        "title": title.strip(),
                        "url": href if href.startswith("http") else f"http://www.news.cn{href}",
                        "intro": "",
                        "media": "新华社",
                        "source": "xinhua"
                    })
            return news_items
    except Exception as e:
        print(f"❌ 新华社获取失败: {e}")
        return []


def fetch_gov_cn() -> List[Dict]:
    """获取中国政府网最新政策"""
    url = "http://www.gov.cn/zhengce/zhengceku/"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as response:
            html = response.read().decode("utf-8")
            news_items = []
            # 匹配政策标题
            pattern = r'<li[^\u003e]*\u003e[^\u003c]*<a[^\u003e]*href="([^"]+)"[^\u003e]*\u003e([^\u003c]+)</a\u003e'
            matches = re.findall(pattern, html)
            for href, title in matches[:20]:
                if len(title.strip()) > 10 and "政策" in title or "国务院" in title or "通知" in title:
                    news_items.append({
                        "title": title.strip(),
                        "url": href if href.startswith("http") else f"http://www.gov.cn{href}",
                        "intro": "",
                        "media": "中国政府网",
                        "source": "gov_cn"
                    })
            return news_items
    except Exception as e:
        print(f"❌ 政府网获取失败: {e}")
        return []


def fetch_pbc() -> List[Dict]:
    """获取中国人民银行公告"""
    url = "http://www.pbc.gov.cn/zhengcehuobisi/11140/index.html"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as response:
            html = response.read().decode("utf-8")
            news_items = []
            # 匹配公告标题
            pattern = r'<a[^\u003e]*href="([^"]+)"[^\u003e]*\u003e([^\u003c]*(?:降准|降息|LPR|MLF|公开市场|货币政策|利率)[^\u003c]*)</a\u003e'
            matches = re.findall(pattern, html)
            for href, title in matches[:10]:
                news_items.append({
                    "title": title.strip(),
                    "url": href if href.startswith("http") else f"http://www.pbc.gov.cn{href}",
                    "intro": "",
                    "media": "中国人民银行",
                    "source": "pbc"
                })
            return news_items
    except Exception as e:
        print(f"❌ 央行获取失败: {e}")
        return []


def fetch_csrc() -> List[Dict]:
    """获取证监会公告"""
    url = "http://www.csrc.gov.cn/csrc/c100028/common_list.shtml"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as response:
            html = response.read().decode("utf-8")
            news_items = []
            # 匹配公告标题
            pattern = r'<a[^\u003e]*href="([^"]+)"[^\u003e]*\u003e([^\u003c]*(?:证监会|监管|IPO|上市|退市|处罚|问询|改革)[^\u003c]*)</a\u003e'
            matches = re.findall(pattern, html)
            for href, title in matches[:10]:
                news_items.append({
                    "title": title.strip(),
                    "url": href if href.startswith("http") else f"http://www.csrc.gov.cn{href}",
                    "intro": "",
                    "media": "证监会",
                    "source": "csrc"
                })
            return news_items
    except Exception as e:
        print(f"❌ 证监会获取失败: {e}")
        return []


def dedup_news(all_news: List[Dict]) -> List[Dict]:
    unique = []
    for news in all_news:
        is_dup = False
        for u in unique:
            if news["title"] in u["title"] or u["title"] in news["title"] or news["title"][:20] == u["title"][:20]:
                is_dup = True
                break
        if not is_dup:
            unique.append(news)
    return unique


def ai_analyze_news_batch(news_list: List[Dict]) -> Dict[int, Dict]:
    if not DEEPSEEK_API_KEY:
        print("⚠️ DeepSeek API Key未配置")
        return {i: {"importance": "low", "sectors": [], "reason": ""} for i in range(len(news_list))}
    
    news_text = "\n".join([
        f"{i+1}. [{n['source']}] {n['title']}"
        for i, n in enumerate(news_list[:15])
    ])
    
    prompt = f"""你是资深财经分析师。分析以下官方渠道新闻对A股市场或中国经济的直接影响。

## 新闻列表
{news_text}

## 要求
对每条新闻判断：
1. 重要性（urgent/high/normal/low）
2. 影响板块（如有）
3. 一句话理由

输出严格JSON：
{{"analysis": [{{"index": 1, "importance": "urgent", "sectors": ["航天军工"], "reason": "中国首次火箭回收成功"}}]}}

规则：
- urgent: 重大政策、历史首次、影响大盘2%+
- high: 行业重大、知名企业变动
- normal/low: 常规公告、无实质影响
"""
    
    try:
        req_data = json.dumps({
            "model": DEEPSEEK_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
            "max_tokens": 2000
        }).encode("utf-8")
        
        req = urllib.request.Request(
            f"{DEEPSEEK_BASE_URL}/chat/completions",
            data=req_data,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {DEEPSEEK_API_KEY}"}
        )
        
        with urllib.request.urlopen(req, timeout=30) as response:
            result = json.loads(response.read().decode("utf-8"))
            content = result["choices"][0]["message"]["content"]
            
            text = content.strip()
            if text.startswith("```json"):
                text = text[7:]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()
            
            analysis = json.loads(text)
            results = {}
            for item in analysis.get("analysis", []):
                idx = item.get("index", 1) - 1
                if 0 <= idx < len(news_list):
                    results[idx] = {
                        "importance": item.get("importance", "normal"),
                        "sectors": item.get("sectors", []),
                        "reason": item.get("reason", "")
                    }
            
            for i in range(len(news_list)):
                if i not in results:
                    results[i] = {"importance": "normal", "sectors": [], "reason": ""}
            
            return results
    except Exception as e:
        print(f"⚠️ AI分析失败: {e}")
        return {i: {"importance": "normal", "sectors": [], "reason": ""} for i in range(len(news_list))}


def format_alert(news: Dict, analysis: Dict) -> str:
    importance_emoji = {"urgent": "🔴", "high": "🟠", "normal": "🟡", "low": "🟢"}
    emoji = importance_emoji.get(analysis["importance"], "🟢")
    
    sectors_str = ""
    if analysis.get("sectors"):
        sectors_str = f"\n\n📈 板块: {', '.join(analysis['sectors'])}"
    
    reason_str = ""
    if analysis.get("reason"):
        reason_str = f"\n💡 {analysis['reason']}"
    
    return (
        f"{emoji} **【第一手】重大事件** [{news['source']}]\n\n"
        f"{news['title']}\n\n"
        f"📰 {news['media']} | 🕐 {datetime.now(timezone(timedelta(hours=8))).strftime('%H:%M')}"
        f"{reason_str}"
        f"{sectors_str}"
    )


def queue_alert(msg: str) -> bool:
    import fcntl
    try:
        pending_file = os.path.join(ALERTS_DIR, "pending_alerts.json")
        with open(pending_file, "r+") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                alerts = json.load(f) if os.path.getsize(pending_file) > 0 else []
            except json.JSONDecodeError:
                alerts = []
            alerts.append({
                "time": datetime.now(timezone(timedelta(hours=8))).isoformat(),
                "msg": msg
            })
            f.seek(0)
            f.truncate()
            json.dump(alerts, f, ensure_ascii=False, indent=2)
            fcntl.flock(f, fcntl.LOCK_UN)
        return True
    except FileNotFoundError:
        try:
            os.makedirs(ALERTS_DIR, exist_ok=True)
            with open(pending_file, "w") as f:
                json.dump([{
                    "time": datetime.now(timezone(timedelta(hours=8))).isoformat(),
                    "msg": msg
                }], f, ensure_ascii=False, indent=2)
            return True
        except:
            return False
    except:
        return False


def save_news(news_list: List[Dict]):
    ensure_dirs()
    now = datetime.now(timezone(timedelta(hours=8)))
    with open(FIRSTHAND_FILE, "a", encoding="utf-8") as f:
        for news in news_list:
            record = {
                "time": now.isoformat(),
                "title": news["title"],
                "url": news["url"],
                "media": news["media"],
                "source": news.get("source", "unknown"),
                "ai_importance": news.get("ai_importance"),
            }
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


def main():
    print(f"=== 第一手重大事件采集 | {datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S')} ===")
    
    # 多源采集
    xinhua = fetch_xinhua()
    gov = fetch_gov_cn()
    pbc = fetch_pbc()
    csrc = fetch_csrc()
    
    print(f"✅ 新华社: {len(xinhua)} 条")
    print(f"✅ 政府网: {len(gov)} 条")
    print(f"✅ 央行: {len(pbc)} 条")
    print(f"✅ 证监会: {len(csrc)} 条")
    
    all_news = dedup_news(xinhua + gov + pbc + csrc)
    print(f"✅ 去重后: {len(all_news)} 条")
    
    if not all_news:
        print("❌ 未获取到新闻")
        return 1
    
    # AI分析
    print(f"\n🧠 AI分析 {len(all_news)} 条...")
    ai_results = ai_analyze_news_batch(all_news)
    
    urgent_news = []
    for i, news in enumerate(all_news):
        analysis = ai_results.get(i, {"importance": "normal", "sectors": [], "reason": ""})
        news["ai_importance"] = analysis["importance"]
        news["ai_sectors"] = analysis["sectors"]
        news["ai_reason"] = analysis["reason"]
        
        if analysis["importance"] in ["urgent", "high"]:
            urgent_news.append((news, analysis))
            print(f"{analysis['importance'].upper()}: {news['title'][:50]}...")
    
    save_news(all_news)
    print(f"\n✅ 已保存到 {FIRSTHAND_FILE}")
    
    if urgent_news:
        print(f"\n🚨 发现 {len(urgent_news)} 条重要事件")
        for news, analysis in urgent_news[:5]:
            alert_msg = format_alert(news, analysis)
            queue_alert(alert_msg)
            print(f"📤 已推送: {news['title'][:40]}...")
    else:
        print("ℹ️ 无重要事件")
    
    print(f"\n=== 采集完成 ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
