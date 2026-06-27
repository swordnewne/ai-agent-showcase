#!/usr/bin/env python3
"""
金融信号预警系统 - MVP v0.2
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
import logging
import argparse
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional
import requests

# ========== 初始化日志 ==========
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"
DATE_FORMAT = "%H:%M:%S"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT, datefmt=DATE_FORMAT)
logger = logging.getLogger("finance_signal")

# ========== 配置加载 ==========
WORKSPACE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CONFIG_PATH = os.path.join(WORKSPACE, "config", "finance_signals.json")
STOCK_POOL_PATH = os.path.join(WORKSPACE, "config", "stock_pool.json")


def load_config() -> Dict:
    """加载主配置文件"""
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        logger.warning(f"配置文件不存在: {CONFIG_PATH}，使用默认配置")
        return _default_config()
    except json.JSONDecodeError as e:
        logger.error(f"配置文件格式错误: {e}")
        return _default_config()


def _default_config() -> Dict:
    """默认配置（兜底）"""
    return {
        "data_source": {
            "sina_api": "https://hq.sinajs.cn/list={}",
            "sina_referer": "https://finance.sina.com.cn"
        },
        "thresholds": {
            "volume_ratio": 2.0,
            "price_change_big": 5.0,
            "turnover_min": 1e9,
            "index_change": 2.0,
            "index_panic": 2.0,
            "price_change_limit": 9.5,
            "cooldown_seconds": 600
        },
        "index_codes": {
            "sh000300": "沪深300",
            "sz399006": "创业板指",
            "sh000001": "上证指数"
        },
        "cache": {"file": ".alerts/finance_signals.json"},
        "retry": {"max_attempts": 3, "base_delay": 1.0, "max_delay": 8.0}
    }


def load_stock_pool() -> List[str]:
    """加载股票池"""
    try:
        with open(STOCK_POOL_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data.get("all", [])
    except FileNotFoundError:
        logger.warning(f"股票池文件不存在: {STOCK_POOL_PATH}，使用内置默认值")
        return _default_stock_pool()
    except json.JSONDecodeError as e:
        logger.error(f"股票池格式错误: {e}")
        return _default_stock_pool()


def _default_stock_pool() -> List[str]:
    """默认股票池（兜底）"""
    return [
        "600519", "300750", "600030", "000333", "002594", "600276", "600887", "000858",
        "300760", "601899", "601012", "601318", "600309", "600036", "601288",
        "300308", "300418", "002475", "300014", "603501", "002230", "300033", "002460",
        "002371", "603259", "300347", "300124", "002008", "601888", "600999", "300759",
        "300454", "002812", "300433", "002241", "002142", "300059", "601633", "600438",
        "603019", "600588", "000977", "002179", "000625", "688235",
        "688256", "688041", "688111", "688012", "300496", "300223", "688599", "688390",
        "002151", "300045", "300476", "300502", "300638", "300682", "300782", "688036",
        "300661", "300474", "002236", "300413", "603444", "002405", "300144", "300003",
        "600745",
    ]


# 加载配置
CONFIG = load_config()
THRESHOLDS = CONFIG["thresholds"]
INDEX_CODES = CONFIG["index_codes"]
SINA_API = CONFIG["data_source"]["sina_api"]
SINA_REFERER = CONFIG["data_source"]["sina_referer"]
RETRY_CONFIG = CONFIG["retry"]

# 缓存文件路径
ALERT_LOG = os.path.join(WORKSPACE, CONFIG["cache"]["file"])
os.makedirs(os.path.dirname(ALERT_LOG), exist_ok=True)

# 股票池（从配置热加载）
STOCK_POOL = load_stock_pool()


# ========== 工具函数 ==========

def load_sent_cache() -> Dict:
    """加载已发送信号的缓存（防重复）"""
    try:
        with open(ALERT_LOG, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_sent_cache(cache: Dict):
    """保存已发送信号缓存"""
    with open(ALERT_LOG, "w", encoding="utf-8") as f:
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


def _retry_with_backoff(func, *args, **kwargs):
    """指数退避重试装饰器"""
    max_attempts = RETRY_CONFIG["max_attempts"]
    base_delay = RETRY_CONFIG["base_delay"]
    max_delay = RETRY_CONFIG["max_delay"]
    
    last_exception = None
    for attempt in range(1, max_attempts + 1):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            last_exception = e
            if attempt >= max_attempts:
                break
            delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
            logger.warning(f"请求失败（第{attempt}次）: {e}，{delay}s后重试...")
            time.sleep(delay)
    
    logger.error(f"请求失败（共{max_attempts}次）: {last_exception}")
    return None


# ========== 数据获取 ==========

def fetch_sina_quotes(codes: List[str]) -> Dict[str, Dict]:
    """从新浪财经获取实时行情（带重试）
    
    返回: {code: {name, price, change_pct, volume, turnover, high, low, open, pre_close}}
    """
    if not codes:
        return {}
    
    # 新浪要求code格式: sh600519, sz000858
    sina_codes = []
    for c in codes:
        if c.startswith(("sh", "sz")):
            sina_codes.append(c)
        else:
            prefix = "sh" if c.startswith(("6", "688")) else "sz"
            sina_codes.append(f"{prefix}{c}")
    
    url = SINA_API.format(",".join(sina_codes))
    headers = {"Referer": SINA_REFERER}
    
    def _do_request():
        r = requests.get(url, headers=headers, timeout=10)
        r.encoding = "gb2312"
        return r.text
    
    text = _retry_with_backoff(_do_request)
    if text is None:
        return {}
    
    results = {}
    for line in text.strip().split(";"):
        line = line.strip()
        if not line or "=" not in line:
            continue
        
        try:
            code_part, data_part = line.split("=", 1)
            code = code_part.split("_")[-1]  # sh600519 → 600519
            data = data_part.strip('"').split(",")
            
            if len(data) < 30:
                continue
            
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
        except (ValueError, IndexError) as e:
            logger.debug(f"解析行情数据出错: {e} | 行: {line[:50]}...")
            continue
    
    return results


def fetch_index_quotes() -> Dict[str, Dict]:
    """获取大盘指数实时行情"""
    index_sina_codes = ["sh000300", "sz399006", "sh000001"]
    return fetch_sina_quotes(index_sina_codes)


# ========== 信号检测 ==========

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


def check_news_signals() -> List[Dict]:
    """检查突发新闻信号（MVP阶段预留接口）"""
    return []


# ========== 推送格式化 ==========

def format_alert(signal: Dict) -> str:
    """格式化推送消息"""
    level_emoji = {"urgent": "🔴", "warning": "🟡", "info": "🔵"}
    emoji = level_emoji.get(signal.get("level", "info"), "🔵")
    return f"{emoji} {signal['title']}\n\n{signal['content']}"


def send_wechat_alert(msg: str) -> bool:
    """发送微信推送（通过写文件+独立脚本读取）"""
    try:
        pending_file = os.path.join(WORKSPACE, ".alerts", "pending_wechat.json")
        alerts = []
        if os.path.exists(pending_file):
            with open(pending_file, "r", encoding="utf-8") as f:
                alerts = json.load(f)
        alerts.append({
            "time": datetime.now(timezone(timedelta(hours=8))).isoformat(),
            "msg": msg
        })
        with open(pending_file, "w", encoding="utf-8") as f:
            json.dump(alerts, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        logger.error(f"写入推送队列失败: {e}")
        return False


# ========== 主流程 ==========

def run_realtime_scan():
    """实时扫描（交易时段）"""
    now = datetime.now(timezone(timedelta(hours=8)))
    logger.info(f"{'='*50}")
    logger.info(f"金融信号实时扫描开始 | {now.strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"股票池: {len(STOCK_POOL)}只 | 缓存: {ALERT_LOG}")
    logger.info(f"{'='*50}")
    
    cache = load_sent_cache()
    signals = []
    
    # 1. 获取个股行情
    logger.info("获取个股行情...")
    stock_quotes = fetch_sina_quotes(STOCK_POOL)
    if not stock_quotes:
        logger.error("个股行情获取失败，本次扫描终止")
        return []
    logger.info(f"成功获取 {len(stock_quotes)} 只个股行情")
    
    # 2. 获取大盘指数
    logger.info("获取大盘指数...")
    index_quotes = fetch_index_quotes()
    if not index_quotes:
        logger.warning("大盘指数获取失败，跳过指数检测")
    else:
        logger.info(f"成功获取 {len(index_quotes)} 个指数行情")
    
    # 3. 判断大盘状态
    market_panic = False
    sh300 = index_quotes.get("sh000300", {})
    if sh300:
        change_300 = sh300.get("change_pct", 0)
        if change_300 <= -THRESHOLDS["index_panic"]:
            market_panic = True
            logger.warning(f"⚠️ 大盘恐慌模式（沪深300 {change_300}%），降噪处理")
    
    # 4. 个股检测
    logger.info("扫描个股信号...")
    for code, data in stock_quotes.items():
        # 涨停/跌停预警（最高优先级，不受恐慌模式影响）
        signal = check_limit_up_down(code, data)
        if signal and is_cooled_down(code, signal["type"], cache):
            signals.append(signal)
            mark_sent(code, signal["type"], cache)
            continue
        
        # 异常放量（恐慌模式下收紧）
        signal = check_volume_anomaly(code, data, {}, market_panic=market_panic)
        if signal and is_cooled_down(code, signal["type"], cache):
            signals.append(signal)
            mark_sent(code, signal["type"], cache)
    
    # 5. 大盘检测
    if index_quotes:
        logger.info("扫描大盘信号...")
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
    logger.info(f"扫描完成，发现 {len(signals)} 个信号")
    sent_count = 0
    for sig in signals:
        msg = format_alert(sig)
        logger.info(f"\n{'='*40}\n{msg}\n{'='*40}")
        if send_wechat_alert(msg):
            sent_count += 1
    
    save_sent_cache(cache)
    logger.info(f"推送完成: {sent_count}/{len(signals)} 条")
    if market_panic:
        logger.info("（恐慌模式：仅推送涨跌停和大盘异动）")
    return signals


def run_daily_summary():
    """收盘后日终总结"""
    now = datetime.now(timezone(timedelta(hours=8)))
    logger.info(f"{'='*50}")
    logger.info(f"日终总结 | {now.strftime('%Y-%m-%d')}")
    logger.info(f"{'='*50}")
    
    stock_quotes = fetch_sina_quotes(STOCK_POOL)
    if not stock_quotes:
        logger.error("行情获取失败")
        return
    
    # 统计
    up_count = sum(1 for d in stock_quotes.values() if d["change_pct"] > 0)
    down_count = sum(1 for d in stock_quotes.values() if d["change_pct"] < 0)
    limit_up = [c for c, d in stock_quotes.items() if d["change_pct"] >= 9.5]
    limit_down = [c for c, d in stock_quotes.items() if d["change_pct"] <= -9.5]
    
    summary = (
        f"📊 今日复盘 ({now.strftime('%m-%d')})\n"
        f"上涨: {up_count}只 | 下跌: {down_count}只\n"
        f"涨停: {len(limit_up)}只 | 跌停: {len(limit_down)}只\n"
    )
    if limit_up:
        names = [stock_quotes[c]["name"] for c in limit_up[:5]]
        summary += f"🔥 涨停: {', '.join(names)}{'...' if len(limit_up) > 5 else ''}\n"
    if limit_down:
        names = [stock_quotes[c]["name"] for c in limit_down[:5]]
        summary += f"💥 跌停: {', '.join(names)}{'...' if len(limit_down) > 5 else ''}\n"
    
    logger.info(f"\n{summary}")
    send_wechat_alert(summary)
    return summary


# ========== 入口 ==========

def main():
    parser = argparse.ArgumentParser(description="金融信号预警系统")
    parser.add_argument("--mode", choices=["realtime", "summary"], default="realtime",
                        help="运行模式: realtime(实时检测) / summary(日终总结)")
    parser.add_argument("--debug", action="store_true", help="开启DEBUG日志")
    args = parser.parse_args()
    
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
        logger.debug("DEBUG模式已开启")
    
    if args.mode == "realtime":
        run_realtime_scan()
    else:
        run_daily_summary()


if __name__ == "__main__":
    main()
