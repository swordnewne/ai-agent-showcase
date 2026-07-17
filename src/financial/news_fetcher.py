#!/usr/bin/env python3
"""
财经新闻实时采集器 - Scrapling版 v2.3 FINAL

更新：
1. 新华社双频道：科技 + 财经
2. AI判断标准重构：基于"影响类型"而非关键词
3. 三层过滤机制：
   - 第一层：关键词触发urgent（重大事件）
   - 第二层：AI内容判断high（影响板块）
   - 第三层：代码兜底过滤（分析文章/个股收购/外交活动/程序性会议/海外企业）
4. 推送量：4-6条/轮（89条→6条high/1条urgent）

四源：新浪API / 华尔街见闻API / 新华社(科技+财经) / 中国政府网
"""

import json
import os
import sys
import random
from datetime import datetime, timezone, timedelta
from typing import List, Dict

sys.path.insert(0, '/root/.openclaw/workspace/skills/automation/scrapling-adapter')
from scrapling.fetchers import Fetcher

WORKSPACE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.path.join(WORKSPACE, "data")
ALERTS_DIR = os.path.join(WORKSPACE, ".alerts")
NEWS_FILE = os.path.join(DATA_DIR, "news_realtime.jsonl")

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

# ============ 源配置 ============
SINA_API = "https://feed.mix.sina.com.cn/api/roll/get"
WSJ_API = "https://api-one.wallstcn.com/apiv1/content/articles"
XINHUA_TECH_URL = "http://www.xinhuanet.com/tech/"
XINHUA_FINANCE_URL = "http://www.xinhuanet.com/finance/"
GOV_URL = "https://www.gov.cn/"


def ensure_dirs():
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(ALERTS_DIR, exist_ok=True)


def fetch_sina() -> List[Dict]:
    params = {"pageid": "153", "lid": "2516", "k": "", "num": "100", "r": str(random.randint(100000, 999999))}
    url = f"{SINA_API}?{'&'.join([f'{k}={v}' for k,v in params.items()])}"
    try:
        page = Fetcher.get(url, timeout=10)
        data = page.json()
        if data.get("result", {}).get("status", {}).get("code") != 0:
            return []
        items = data.get("result", {}).get("data", [])
        return [{"title": item.get("title", ""), "intro": item.get("intro", ""), "url": item.get("url", ""),
                 "ctime": item.get("ctime", ""), "media": item.get("media_name", ""), "source": "sina"} for item in items]
    except Exception as e:
        print(f"❌ 新浪: {e}")
        return []


def fetch_wsj() -> List[Dict]:
    params = {"platform": "pc", "client": "chrome", "limit": "20"}
    url = f"{WSJ_API}?{'&'.join([f'{k}={v}' for k,v in params.items()])}"
    try:
        page = Fetcher.get(url, timeout=10)
        data = page.json()
        if data.get("code") != 20000:
            return []
        items = data.get("data", {}).get("items", [])
        return [{"title": item.get("title", ""), "intro": item.get("content_short", ""), "url": item.get("uri", ""),
                 "ctime": str(item.get("display_time", "")), "media": "华尔街见闻", "source": "wsj"} for item in items]
    except Exception as e:
        print(f"❌ WSJ: {e}")
        return []


def fetch_xinhua_single(url: str, media_name: str) -> List[Dict]:
    """新华社单频道抓取"""
    try:
        page = Fetcher.get(url, timeout=10)
        news_items = []
        selectors = ['a[href*="2026"]', 'h3 a', 'h2 a', '.news-list a']
        
        for sel in selectors:
            links = page.css(sel)
            for link in links:
                text = link.text.strip() if link.text else ""
                href = ""
                try:
                    href = link.css('::attr(href)').get() or ""
                except:
                    pass
                if 10 < len(text) < 80 and text not in [n["title"] for n in news_items]:
                    news_items.append({
                        "title": text,
                        "url": href if href.startswith("http") else f"http://www.xinhuanet.com{href}" if href else "",
                        "intro": "", "media": media_name, "source": "xinhua"
                    })
            if len(news_items) >= 10:
                break
        
        if not news_items:
            import re
            pattern = r'<a[^\u003e]*href="([^"]*(?:2026|news\.cn)[^"]*)"[^\u003e]*\u003e([^\u003c]{10,80})\u003c/a\u003e'
            for href, title in re.findall(pattern, page.html_content)[:10]:
                if title.strip() not in [n["title"] for n in news_items]:
                    news_items.append({"title": title.strip(), "url": href, "intro": "", "media": media_name, "source": "xinhua"})
        
        return news_items[:12]
    except Exception as e:
        print(f"❌ {media_name}: {e}")
        return []


def fetch_xinhua() -> List[Dict]:
    """新华社双频道：科技 + 财经"""
    tech_news = fetch_xinhua_single(XINHUA_TECH_URL, "新华社科技")
    finance_news = fetch_xinhua_single(XINHUA_FINANCE_URL, "新华社财经")
    return tech_news + finance_news


def fetch_gov() -> List[Dict]:
    try:
        page = Fetcher.get(GOV_URL, timeout=10)
        news_items = []
        selectors = ['a[href*="zhengce"]', 'a[href*="yaowen"]', 'h3 a', 'h2 a']
        
        for sel in selectors:
            links = page.css(sel)
            for link in links:
                text = link.text.strip() if link.text else ""
                href = ""
                try:
                    href = link.css('::attr(href)').get() or ""
                except:
                    pass
                if 10 < len(text) < 80:
                    if text not in [n["title"] for n in news_items]:
                        news_items.append({
                            "title": text,
                            "url": href if href.startswith("http") else f"https://www.gov.cn{href}" if href else "",
                            "intro": "", "media": "中国政府网", "source": "gov_cn"
                        })
            if len(news_items) >= 8:
                break
        
        return news_items[:10]
    except Exception as e:
        print(f"❌ 政府网: {e}")
        return []


def dedup_news(all_news: List[Dict]) -> List[Dict]:
    unique = []
    for news in all_news:
        is_dup = False
        for u in unique:
            if (news["title"] in u["title"] or u["title"] in news["title"] or
                news["title"][:20] == u["title"][:20]):
                is_dup = True
                if len(news.get("intro", "")) > len(u.get("intro", "")):
                    u["intro"] = news.get("intro", "")
                    u["source"] = f"{u['source']},{news['source']}"
                break
        if not is_dup:
            unique.append(news)
    return unique


def apply_keyword_override(news: Dict) -> Dict:
    """urgent只能通过明确的重大事件关键词触发"""
    text = f"{news['title']} {news.get('intro', '')}"
    
    # urgent: 真正影响全市场的重大事件
    urgent_events = ["央行降准", "央行降息", "LPR下调", "LPR调整", "突发战争", "战争爆发", 
                     "全面制裁", "冲突升级", "大规模空袭", "火箭回收", "首次登月", 
                     "全面推行注册制", "暂停IPO", "IPO暂停", "熔断", "停牌"]
    for kw in urgent_events:
        if kw in text:
            return {"importance": "urgent", "sectors": [], "reason": f"重大事件：{kw}", "impact_type": "market-moving"}
    
    # high: 行业政策/重大政策（仅作为AI的辅助提示，不强制覆盖）
    # 让AI自己判断这些是否应该high
    return {"importance": "normal", "sectors": [], "reason": "", "impact_type": "noise"}


def ai_analyze_all(news_list: List[Dict]) -> Dict[int, Dict]:
    if not DEEPSEEK_API_KEY:
        print("⚠️ DeepSeek API未配置，使用规则分析")
        return {i: apply_keyword_override(n) for i, n in enumerate(news_list)}
    
    all_results = {}
    batch_size = 15
    total = len(news_list)
    batches = (total + batch_size - 1) // batch_size
    
    print(f"🧠 分批AI分析: {total}条 → {batches}批")
    
    for batch_idx in range(batches):
        start = batch_idx * batch_size
        end = min(start + batch_size, total)
        batch = news_list[start:end]
        
        # 关键词强制升级优先
        for local_idx, news in enumerate(batch):
            override = apply_keyword_override(news)
            if override["importance"] in ["urgent", "high"]:
                all_results[start + local_idx] = override
        
        uncovered_indices = [i for i in range(start, end) if i not in all_results]
        if not uncovered_indices:
            continue
        
        uncovered_news = [news_list[i] for i in uncovered_indices]
        
        news_text = "\n".join([
            f"{i+1}. [{n['source']}] {n['title']}\n   {n.get('intro', '')[:100]}"
            for i, n in enumerate(uncovered_news)
        ])
        
        prompt = f"""你是资深财经分析师。请判断以下新闻是否影响A股特定板块。

## 判断标准（基于内容实质，不是关键词）

### 影响板块（impact_type=sector-specific，importance=high）
以下情况：
- 国务院发布针对特定行业的政策（如碳达峰方案→新能源板块，医药规划→医药板块）
- 行业巨头重大变动（如华为被制裁→通信板块，7nm芯片突破→半导体板块）
- 明确影响板块的政策调整（如新能源补贴、医药集采）
- 国际油价因冲突暴涨/暴跌（影响能源板块和通胀预期）
- **板块级重大并购**：锂电行业大额收购、半导体行业整合—— 影响整个板块预期，标high
- **保险资金/国家队大规模加仓特定行业**—— 影响板块流动性预期，标high

### 不影响（impact_type=noise，importance=normal）
以下情况一律不影响：
- 人事变动：摩根大通聘请银行家、高管变动—— 不影响A股
- 外交访问：加拿大总理出访沙特、领导人会见—— 除非明确签订重大能源协议
- 分析师评级：瑞银维持买入、目标价调整、首予增持—— 只是机构观点
- 企业日常：波音认证、SpaceX规划、公司获得某资质—— 不直接影响A股
- 市场统计：上半年港股IPO统计、市场回顾—— 总结性内容，无新信息
- 预测性文章：外储增配香港资产可能会买啥—— 分析推测，不是事实
- 程序性新闻：座谈会、工作推进会、课题入选—— 没有实质政策出台
- 个股事件：某公司拟IPO、被立案、财报发布—— 除非引发行业连锁反应
- 个案收购/要约：仅涉及单家公司，不影响板块—— 标noise
- 海外市场：美股盘前波动、日元走势、美联储某人观点—— 不直接影响A股
- 一般性行业新闻：半导体扩产、AI手机发布—— 没有明确政策或数据支撑
- ⚠️ 分析文章/研报：光大期货"焦煤为何崩了"、中天期货"焦煤短线承压"—— 这是券商分析不是新闻事件，一律noise
- ⚠️ 标题含"如何"、"为何"、"解析"、"分析"、"点评"、"研报"、"期货"—— 说明是分析文章，不是新闻，一律noise

## 新闻列表
{news_text}

## 输出格式（严格JSON）
{{"analysis": [{{"index": 1, "impact_type": "noise", "sectors": [], "reason": "摩根大通人事变动，不影响A股任何板块"}}]}}

## 绝对规则（违反会导致错误）
- 人事变动 = 不影响（no exceptions）
- 外交访问 = 不影响（除非明确签订重大协议）
- 分析师评级 = 不影响（只是观点）
- 海外市场波动 = 不影响（不直接影响A股）
- 分析文章/研报 = 不影响（标题含"如何""为何""解析""分析""点评""研报""期货"）
- 海外企业重大变动：大众砍车型、波音停产、特斯拉裁员—— 除非明确影响中国业务或A股上市公司，一律noise
- 个股收购/要约：某公司拟收购、复牌涨停、套现收购—— 除非引发行业连锁反应，一律noise
- reason必须具体说明"为什么影响/不影响A股板块"
"""
        
        try:
            import urllib.request
            req_data = json.dumps({
                "model": DEEPSEEK_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.2,
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
                for item in analysis.get("analysis", []):
                    local_idx = item.get("index", 1) - 1
                    if 0 <= local_idx < len(uncovered_indices):
                        global_idx = uncovered_indices[local_idx]
                        impact_type = item.get("impact_type", "noise")
                        
                        # 映射：sector-specific→high，其他→normal
                        # urgent只能通过关键词强制触发（重大事件）
                        if impact_type == "sector-specific":
                            importance = "high"
                        else:
                            importance = "normal"
                        
                        all_results[global_idx] = {
                            "importance": importance,
                            "sectors": item.get("sectors", []),
                            "reason": item.get("reason", ""),
                            "impact_type": impact_type
                        }
                
                print(f"  ✅ 批次{batch_idx+1}/{batches} 完成")
                
        except Exception as e:
            print(f"  ⚠️ 批次{batch_idx+1}/{batches} 失败: {e}")
            for idx in uncovered_indices:
                all_results[idx] = {"importance": "normal", "sectors": [], "reason": "", "impact_type": "noise"}
    
    for i in range(total):
        if i not in all_results:
            all_results[i] = apply_keyword_override(news_list[i])
    
    return all_results


def format_alert(news: Dict, analysis: Dict) -> str:
    importance_emoji = {"urgent": "🔴", "high": "🟠", "normal": "🟡", "low": "🟢"}
    emoji = importance_emoji.get(analysis["importance"], "🟢")
    
    sectors_str = ""
    if analysis.get("sectors"):
        sectors_str = f"\n\n📈 板块: {', '.join(analysis['sectors'])}"
    
    reason_str = ""
    if analysis.get("reason"):
        reason_str = f"\n💡 {analysis['reason']}"
    
    source_tag = f"[{news.get('source', 'unknown')}]"
    
    return (
        f"{emoji} **财经快讯** {source_tag}\n\n"
        f"{news['title']}\n\n"
        f"{news.get('intro', '')[:150]}..."
        f"{reason_str}"
        f"{sectors_str}\n\n"
        f"📰 {news.get('media', '未知')} | 🕐 {datetime.now(timezone(timedelta(hours=8))).strftime('%H:%M')}"
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
    with open(NEWS_FILE, "a", encoding="utf-8") as f:
        for news in news_list:
            record = {
                "time": now.isoformat(),
                "title": news["title"],
                "intro": news.get("intro", "")[:200],
                "url": news.get("url", ""),
                "media": news.get("media", ""),
                "source": news.get("source", "unknown"),
                "ai_importance": news.get("ai_importance"),
                "ai_sectors": news.get("ai_sectors"),
                "ai_reason": news.get("ai_reason"),
            }
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


def main():
    print(f"=== Scrapling新闻采集 v2.3 | {datetime.now(timezone(timedelta(hours=8))).strftime('%H:%M:%S')} ===")
    
    sina_news = fetch_sina()
    wsj_news = fetch_wsj()
    xinhua_news = fetch_xinhua()
    gov_news = fetch_gov()
    
    print(f"✅ 新浪: {len(sina_news)} 条")
    print(f"✅ 华尔街见闻: {len(wsj_news)} 条")
    print(f"✅ 新华社: {len(xinhua_news)} 条 (科技+财经)")
    print(f"✅ 政府网: {len(gov_news)} 条")
    
    all_news = dedup_news(sina_news + wsj_news + xinhua_news + gov_news)
    print(f"✅ 去重后: {len(all_news)} 条")
    
    if not all_news:
        print("❌ 未获取到新闻")
        return 1
    
    # 分批AI分析
    ai_results = ai_analyze_all(all_news)
    
    # 整理推送新闻
    urgent_news = []
    for i, news in enumerate(all_news):
        analysis = ai_results.get(i, {"importance": "normal", "sectors": [], "reason": "", "impact_type": "noise"})
        
        # 后过滤：AI可能误标，需代码兜底
        title = news.get("title", "")
        
        # 强制过滤：分析文章一律normal
        analysis_keywords = ["如何", "为何", "解析", "分析", "点评", "研报", "期货", "短线", "承压"]
        if any(kw in title for kw in analysis_keywords):
            analysis = {"importance": "normal", "sectors": [], "reason": "分析文章，非新闻事件", "impact_type": "noise"}
        
        # 强制过滤：程序性会议一律normal
        procedural_keywords = ["工作推进会", "座谈会", "协调会", "培训会", "动员会"]
        if any(kw in title for kw in procedural_keywords):
            analysis = {"importance": "normal", "sectors": [], "reason": "程序性会议，无实质政策出台", "impact_type": "noise"}
        
        # 强制过滤：外交访问/会见/签字仪式一律normal
        diplomatic_keywords = ["外交", "会见", "会谈", "签字仪式", "国事访问", "出席"]
        if any(kw in title for kw in diplomatic_keywords):
            analysis = {"importance": "normal", "sectors": [], "reason": "外交活动，不直接影响A股市场", "impact_type": "noise"}
        
        # 强制过滤：个股收购/要约（非板块级）一律normal
        # 特征：含"复牌涨停""套现""创始人"+公司名，或仅涉及单家公司
        individual_patterns = ["复牌涨停", "套现", "创始人"]
        if any(kw in title for kw in individual_patterns) and any(kw in title for kw in ["收购", "要约"]):
            analysis = {"importance": "normal", "sectors": [], "reason": "个股收购，非板块级并购", "impact_type": "noise"}
        
        # 强制过滤：海外企业重大变动一律normal（除非明确影响中国）
        overseas_companies = ["大众", "波音", "特斯拉", "苹果", "微软", "谷歌", "亚马逊"]
        if any(kw in title for kw in overseas_companies):
            if not any(kw in title for kw in ["中国", "A股", "华为", "中兴"]):
                analysis = {"importance": "normal", "sectors": [], "reason": "海外企业变动，不直接影响A股", "impact_type": "noise"}
        
        news["ai_importance"] = analysis["importance"]
        news["ai_sectors"] = analysis["sectors"]
        news["ai_reason"] = analysis["reason"]
        
        if analysis["importance"] in ["urgent", "high"]:
            urgent_news.append((news, analysis))
            print(f"{analysis['importance'].upper()}: {news['title'][:50]}... [来源: {news.get('source', 'unknown')}] 影响: {analysis.get('impact_type', 'unknown')}")
    
    save_news(all_news)
    print(f"\n✅ 已保存到 {NEWS_FILE}")
    
    # 推送：urgent + HIGH
    urgent_list = [(n, a) for n, a in urgent_news if a["importance"] == "urgent"]
    high_list = [(n, a) for n, a in urgent_news if a["importance"] == "high"]
    
    to_push = urgent_list[:3] + high_list[:3]
    
    if to_push:
        print(f"\n🚨 URGENT: {len(urgent_list)}条, HIGH: {len(high_list)}条")
        print(f"📤 实际推送: {len(to_push)}条")
        for news, analysis in to_push:
            alert_msg = format_alert(news, analysis)
            queue_alert(alert_msg)
            print(f"📤 已推送: {news['title'][:40]}...")
    else:
        print("ℹ️ 无重要新闻")
    
    print(f"\n=== 采集完成 ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
