#!/usr/bin/env python3
"""
金融信号预警系统 - MVP v0.1
数据源：新浪财经实时行情（免费，无需token）

推送信号类型：
1. 个股异常放量（>5日均量2倍）
2. 涨停/跌停预警
3. 大盘异常波动（沪深300/创业板指 ±2%）
4. 重大新闻舆情（DeepSeek分析）

运行方式：
  python signal_pipeline.py --mode realtime  # 交易时段实时检测
  python signal_pipeline.py --mode summary   # 收盘后日终总结
"""

import os
import sys
import json
import time
import hashlib
import argparse
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional
import requests
import urllib.parse

# ========== 配置 ==========
WORKSPACE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CONFIG_PATH = os.path.join(WORKSPACE, "config", "finance_signals.json")
ALERT_LOG = os.path.join(WORKSPACE, ".alerts", "finance_signals.json")
os.makedirs(os.path.dirname(ALERT_LOG), exist_ok=True)

# 股票池：从聚宽策略提取的80只票
STOCK_POOL = [
    # 大盘
    "600519", "300750", "600030", "000333", "002594", "600276", "600887", "000858",
    "300760", "601899", "601012", "601318", "600309", "600036", "601288",
    # 中盘
    "300308", "300418", "002475", "300014", "603501", "002230", "300033", "002460",
    "002371", "603259", "300347", "300124", "002008", "601888", "600999", "300759",
    "300454", "002812", "300433", "002241", "002142", "300059", "601633", "600438",
    "603019", "600588", "000977", "002179", "000625", "688235",
    # 小盘
    "688256", "688041", "688111", "688012", "300496", "300223", "688599", "688390",
    "002151", "300045", "300476", "300502", "300638", "300682", "300782", "688036",
    "300661", "300474", "002236", "300413", "603444", "002405", "300144", "300003",
    "600745",
]

# 指数代码
INDEX_CODES = {
    "sh000300": "沪深300",
    "sz399006": "创业板指",
    "sh000001": "上证指数",
}

# 新浪API
SINA_API = "https://hq.sinajs.cn/list={}"
SINA_REFERER = "https://finance.sina.com.cn"

# 异常阈值
THRESHOLDS = {
    "volume_ratio": 2.0,        # 成交量 > 5日均量2倍（MVP暂用成交额阈值）
    "price_change_big": 5.0,    # 涨跌幅 ≥5% 才推（收紧）
    "turnover_min": 1e9,        # 成交额 ≥10亿（收紧）
    "index_change": 2.0,        # 大盘涨跌幅 ±2%
    "index_panic": 2.0,         # 大盘跌≥2%时进入恐慌模式（降噪）
    "price_change_limit": 9.5,  # 接近涨跌停 ±9.5%
    "cooldown_seconds": 600,    # 同一标的冷却10分钟（延长）
}


def load_sent_cache() -> Dict:
    """加载已发送信号的缓存（防重复）"""
    try:
        with open(ALERT_LOG, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_sent_cache(cache: Dict):
    """保存已发送信号缓存"""
    with open(ALERT_LOG, "w") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def is_cooled_down(stock_code: str, signal_type: str, cache: Dict) -> bool:
    """检查是否过了冷却期"""
    key = f"{stock_code}:{signal_type}"
    last_sent = cache.get(key, 0)
    return (time.time() - last_sent) > THRESHOLDS["cooldown_seconds"]


def mark_sent(stock_code: str, signal_type: str, cache: Dict):
    """标记已发送"""
    key = f"{stock_code}:{signal_type}"
    cache[key] = time.time()


def fetch_sina_quotes(codes: List[str]) -> Dict[str, Dict]:
    """从新浪财经获取实时行情
    
    返回: {code: {name, price, change_pct, volume, turnover, high, low, open, pre_close}}
    """
    # 新浪要求code格式: sh600519, sz000858
    sina_codes = []
    for c in codes:
        if c.startswith(("sh", "sz")):
            # 已经是新浪格式
            sina_codes.append(c)
        else:
            prefix = "sh" if c.startswith("6") or c.startswith("688") else "sz"
            sina_codes.append(f"{prefix}{c}")
    
    url = SINA_API.format(",".join(sina_codes))
    headers = {"Referer": SINA_REFERER}
    
    try:
        r = requests.get(url, headers=headers, timeout=10)
        r.encoding = "gb2312"
        text = r.text
    except Exception as e:
        print(f"[ERROR] 获取行情失败: {e}")
        return {}
    
    results = {}
    for line in text.strip().split(";"):
        line = line.strip()
        if not line or "=" not in line:
            continue
        
        code_part, data_part = line.split("=", 1)
        code = code_part.split("_")[-1]  # sh600519 → 600519
        data = data_part.strip('"').split(",")
        
        if len(data) < 30:
            continue
        
        # 新浪数据字段解析（顺序固定）
        # 0:名称, 1:今日开盘价, 2:昨日收盘价, 3:当前价, 4:今日最高价, 5:今日最低价
        # 6:买一价, 7:卖一价, 8:成交量(股), 9:成交额(元)
        # ...后面是买卖盘，不需要
        name = data[0]
        open_price = float(data[1]) if data[1] else 0
        pre_close = float(data[2]) if data[2] else 0
        price = float(data[3]) if data[3] else 0
        high = float(data[4]) if data[4] else 0
        low = float(data[5]) if data[5] else 0
        volume = int(data[8]) if data[8] else 0
        turnover = float(data[9]) if data[9] else 0
        
        change_pct = round((price - pre_close) / pre_close * 100, 2) if pre_close else 0
        
        results[code] = {
            "name": name,
            "price": price,
            "open": open_price,
            "high": high,
            "low": low,
            "pre_close": pre_close,
            "change_pct": change_pct,
            "volume": volume,
            "turnover": turnover,
        }
    
    return results


def fetch_index_quotes() -> Dict[str, Dict]:
    """获取大盘指数实时行情"""
    # 指数代码直接用新浪格式
    index_sina_codes = ["sh000300", "sz399006", "sh000001"]
    return fetch_sina_quotes(index_sina_codes)


def check_volume_anomaly(stock_code: str, data: Dict, history: Dict, market_panic: bool = False) -> Optional[Dict]:
    """检查个股异常放量/缩量
    
    参数:
        market_panic: 大盘恐慌模式（跌≥2%），此时收紧推送
    """
    change_pct = data["change_pct"]
    turnover = data["turnover"]
    
    # 恐慌模式：只推涨停/跌停（由check_limit_up_down处理），这里跳过
    if market_panic and abs(change_pct) < THRESHOLDS["price_change_limit"]:
        return None
    
    # 正常模式：涨幅≥5% 且 成交额≥10亿
    if abs(change_pct) >= THRESHOLDS["price_change_big"] and turnover >= THRESHOLDS["turnover_min"]:
        direction = "📈" if change_pct > 0 else "📉"
        return {
            "type": "volume_anomaly",
            "level": "warning",
            "title": f"{direction} {data['name']}({stock_code}) 异动",
            "content": (
                f"价格: {data['price']:.2f} ({'+' if change_pct > 0 else ''}{change_pct}%)\n"
                f"成交额: {turnover/1e8:.1f}亿\n"
                f"振幅: {(data['high']-data['low'])/data['pre_close']*100:.1f}%"
            ),
        }
    return None


def check_limit_up_down(stock_code: str, data: Dict) -> Optional[Dict]:
    """检查涨停/跌停预警"""
    change_pct = data["change_pct"]
    
    # 科创板/创业板涨停20%，主板10%
    is_kc_cy = stock_code.startswith("688") or stock_code.startswith("300")
    limit_pct = 20.0 if is_kc_cy else 10.0
    
    # 接近涨停/跌停（±9.5% 或 ±19%）
    near_limit = limit_pct - 0.5
    
    if change_pct >= near_limit:
        return {
            "type": "limit_up",
            "level": "urgent",
            "title": f"🚀 {data['name']}({stock_code}) 接近涨停！",
            "content": (
                f"当前: {data['price']:.2f} (+{change_pct}%)\n"
                f"涨停价: {data['pre_close'] * (1 + limit_pct/100):.2f}\n"
                f"距涨停: {(limit_pct - change_pct):.2f}%"
            ),
        }
    elif change_pct <= -near_limit:
        return {
            "type": "limit_down",
            "level": "urgent",
            "title": f"💥 {data['name']}({stock_code}) 接近跌停！",
            "content": (
                f"当前: {data['price']:.2f} ({change_pct}%)\n"
                f"跌停价: {data['pre_close'] * (1 - limit_pct/100):.2f}\n"
                f"距跌停: {abs(limit_pct + change_pct):.2f}%"
            ),
        }
    return None


def check_index_anomaly(index_code: str, data: Dict) -> Optional[Dict]:
    """检查大盘异常波动"""
    change_pct = data["change_pct"]
    name = INDEX_CODES.get(index_code, index_code)
    
    if abs(change_pct) >= THRESHOLDS["index_change"]:
        direction = "📈" if change_pct > 0 else "📉"
        return {
            "type": "index_anomaly",
            "level": "warning",
            "title": f"{direction} {name} 大幅波动 {change_pct}%",
            "content": (
                f"当前: {data['price']:.2f}\n"
                f"涨跌: {'+' if change_pct > 0 else ''}{change_pct}%\n"
                f"振幅: {(data['high']-data['low'])/data['pre_close']*100:.1f}%"
            ),
        }
    return None


def fetch_stock_news(stock_code: str, stock_name: str) -> List[Dict]:
    """获取个股最新新闻（东方财富）
    
    返回最近3条相关新闻
    """
    # 东方财富个股新闻API
    # 格式: https://searchapi.eastmoney.com/api/suggest/get?input=600519&type=14&count=3
    # 或者直接用页面爬取
    
    # 简化：用新浪财经的个股新闻RSS（更稳定）
    # 实际生产建议用付费API或自建爬虫
    
    # MVP阶段：返回空，新闻渠道后续接入
    # 原因：免费新闻API结构不稳定，需要单独维护
    return []


def check_news_signals() -> List[Dict]:
    """检查突发新闻信号
    
    待接入：
    - 东方财富快讯API
    - 财联社电报
    - 上市公司公告（巨潮资讯）
    - 宏观政策（央行/证监会）
    
    MVP阶段返回空列表，预留接口
    """
    # TODO: 接入新闻源
    # 方案1: AKShare stock_news_em() - 需要稳定网络
    # 方案2: 东方财富搜索API - 结构可能变化
    # 方案3: 自建爬虫 - 需要反爬策略
    return []


def format_alert(signal: Dict) -> str:
    """格式化推送消息"""
    now = datetime.now(timezone(timedelta(hours=8)))
    time_str = now.strftime("%H:%M")
    
    lines = [
        f"【交易预警 {time_str}】",
        f"",
        f"{signal['title']}",
        f"",
        f"{signal['content']}",
    ]
    
    if signal["type"] in ("limit_up", "limit_down"):
        lines.append("")
        lines.append("⚠️ 注意止盈止损")
    
    return "\n".join(lines)


def send_wechat_alert(message: str) -> bool:
    """发送微信推送"""
    # 通过OpenClaw的message工具发送
    # 这里写文件，由外部定时任务读取后调用message工具
    alert_file = os.path.join(WORKSPACE, ".alerts", "pending_wechat.json")
    try:
        alerts = []
        if os.path.exists(alert_file):
            with open(alert_file, "r") as f:
                alerts = json.load(f)
        alerts.append({
            "timestamp": datetime.now(timezone(timedelta(hours=8))).isoformat(),
            "message": message,
        })
        with open(alert_file, "w") as f:
            json.dump(alerts, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"[ERROR] 保存推送消息失败: {e}")
        return False


def run_realtime_scan():
    """交易时段实时扫描"""
    print(f"[{datetime.now(timezone(timedelta(hours=8))).strftime('%H:%M:%S')}] 开始扫描...")
    
    cache = load_sent_cache()
    signals = []
    
    # 1. 获取股票池行情
    stock_quotes = fetch_sina_quotes(STOCK_POOL)
    print(f"  获取 {len(stock_quotes)} 只股票行情")
    
    # 2. 获取大盘指数
    index_quotes = fetch_index_quotes()
    print(f"  获取 {len(index_quotes)} 个指数行情")
    
    # 3. 判断大盘状态（是否恐慌）
    market_panic = False
    sh300 = index_quotes.get("sh000300", {})
    if sh300:
        change_300 = sh300.get("change_pct", 0)
        if change_300 <= -THRESHOLDS["index_panic"]:
            market_panic = True
            print(f"  ⚠️ 大盘恐慌模式（沪深300 {change_300}%），降噪处理")
    
    # 4. 个股检测
    for code, data in stock_quotes.items():
        # 涨停/跌停预警（最高优先级，不受恐慌模式影响）
        signal = check_limit_up_down(code, data)
        if signal and is_cooled_down(code, signal["type"], cache):
            signals.append(signal)
            mark_sent(code, signal["type"], cache)
            continue  # 涨停了就不需要再检查放量了
        
        # 异常放量（恐慌模式下收紧）
        signal = check_volume_anomaly(code, data, {}, market_panic=market_panic)
        if signal and is_cooled_down(code, signal["type"], cache):
            signals.append(signal)
            mark_sent(code, signal["type"], cache)
    
    # 5. 大盘检测
    for code, data in index_quotes.items():
        signal = check_index_anomaly(code, data)
        if signal and is_cooled_down(code, signal["type"], cache):
            signals.append(signal)
            mark_sent(code, signal["type"], cache)
    
    # 6. 新闻信号（预留）
    news_signals = check_news_signals()
    for sig in news_signals:
        if is_cooled_down("news", sig.get("type", "news"), cache):
            signals.append(sig)
            mark_sent("news", sig.get("type", "news"), cache)
    
    # 7. 推送
    sent_count = 0
    for sig in signals:
        msg = format_alert(sig)
        print(f"\n{'='*40}\n{msg}\n{'='*40}")
        if send_wechat_alert(msg):
            sent_count += 1
    
    save_sent_cache(cache)
    print(f"\n扫描完成，发现 {len(signals)} 个信号，推送 {sent_count} 条")
    if market_panic:
        print("  （恐慌模式：仅推送涨跌停和大盘异动）")
    return signals


def run_daily_summary():
    """收盘后日终总结"""
    print("生成日终总结...")
    
    stock_quotes = fetch_sina_quotes(STOCK_POOL)
    index_quotes = fetch_index_quotes()
    
    # 统计涨跌停
    limit_up = []
    limit_down = []
    big_movers = []  # 涨跌幅>5%
    
    for code, data in stock_quotes.items():
        cp = data["change_pct"]
        if cp >= 9.5:
            limit_up.append((code, data))
        elif cp <= -9.5:
            limit_down.append((code, data))
        elif abs(cp) >= 5:
            big_movers.append((code, data))
    
    # 按涨跌幅排序
    top_gainers = sorted(stock_quotes.items(), key=lambda x: x[1]["change_pct"], reverse=True)[:5]
    top_losers = sorted(stock_quotes.items(), key=lambda x: x[1]["change_pct"])[:5]
    
    now = datetime.now(timezone(timedelta(hours=8)))
    lines = [
        f"【{now.strftime('%m月%d日')} 收盘总结】",
        "",
        "📊 大盘",
    ]
    for code, data in index_quotes.items():
        name = INDEX_CODES.get(code, code)
        cp = data["change_pct"]
        emoji = "📈" if cp > 0 else "📉"
        lines.append(f"  {emoji} {name}: {data['price']:.2f} ({'+' if cp > 0 else ''}{cp}%)")
    
    lines.extend([
        "",
        f"🔥 涨停 {len(limit_up)} 只",
        f"💥 跌停 {len(limit_down)} 只",
        f"📈📉 涨跌幅>5%: {len(big_movers)} 只",
        "",
        "🏆 涨幅TOP5",
    ])
    for code, data in top_gainers:
        lines.append(f"  {data['name']}({code}): +{data['change_pct']}%")
    
    lines.append("")
    lines.append("📉 跌幅TOP5")
    for code, data in top_losers:
        lines.append(f"  {data['name']}({code}): {data['change_pct']}%")
    
    msg = "\n".join(lines)
    print(f"\n{'='*40}\n{msg}\n{'='*40}")
    send_wechat_alert(msg)
    return msg


def main():
    parser = argparse.ArgumentParser(description="金融信号预警系统")
    parser.add_argument("--mode", choices=["realtime", "summary"], default="realtime",
                        help="运行模式: realtime=实时检测, summary=日终总结")
    args = parser.parse_args()
    
    if args.mode == "realtime":
        run_realtime_scan()
    else:
        run_daily_summary()


if __name__ == "__main__":
    main()
