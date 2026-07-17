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
import sqlite3
import re
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional
import requests

# ========== 初始化日志 ==========
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"
DATE_FORMAT = "%H:%M:%S"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT, datefmt=DATE_FORMAT)
logger = logging.getLogger("finance_signal")

# ========== 配置加载 ==========
def _find_workspace() -> str:
    """Find project root by looking for marker files"""
    path = os.path.dirname(os.path.abspath(__file__))
    while path != '/':
        if os.path.exists(os.path.join(path, 'AGENTS.md')) or os.path.exists(os.path.join(path, 'SOUL.md')):
            return path
        path = os.path.dirname(path)
    # Fallback: script-relative (3 levels up for src/financial/ path)
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

WORKSPACE = _find_workspace()
CONFIG_PATH = os.path.join(WORKSPACE, "config", "finance_signals.json")
STOCK_POOL_PATH = os.path.join(WORKSPACE, "config", "stock_pool.json")

# ========== 信号记录到复盘数据库（新增：2026-06-28）==========
_FINANCE_DB = os.path.join(WORKSPACE, "data", "finance.db")


# 防御：确保数据库目录存在，避免 sqlite3 报 "unable to open database file"
os.makedirs(os.path.dirname(_FINANCE_DB), exist_ok=True)


def _extract_code_from_title(title: str) -> str:
    """从标题中提取股票代码，如 '寒武纪(sh688256)' -> '688256'"""
    # 匹配 (688256) 或 (sh688256) 或 (sz002475)
    m = re.search(r'\((?:sh|sz)?([0-9]{6})\)', title)
    return m.group(1) if m else ""


def _extract_price_from_content(content: str) -> float:
    """从内容中提取当前价格，如 '当前: 1480.50' -> 1480.5"""
    # 同时匹配 "当前: 1480.50" 和 "价格: 1480.50"
    m = re.search(r'(?:当前|价格)[:：]\s*([0-9]+\.?[0-9]*)', content)
    return float(m.group(1)) if m else 0.0


def _infer_signal_type(sig_type: str, title: str) -> str:
    """根据信号类型推断买卖方向
    
    volume_anomaly 需根据标题中的 📈/📉 判断涨跌方向，
    不能无条件标记为 buy。
    """
    if sig_type == "limit_up":
        return "buy"
    elif sig_type == "limit_down":
        return "sell"
    elif sig_type == "volume_anomaly":
        # 根据标题中的 emoji 判断方向：📈/🚀 = 涨/buy，📉/💥 = 跌/sell
        if any(e in title for e in ("📈", "🚀", "📊", "🔥")):
            return "buy"
        elif any(e in title for e in ("📉", "💥", "🔻")):
            return "sell"
        # 兜底：标题含"涨"字买，含"跌"字卖
        elif "涨" in title or "突破" in title:
            return "buy"
        elif "跌" in title or "跌停" in title:
            return "sell"
        return "buy"  # 最终兜底，保持保守
    elif "涨" in title or "突破" in title:
        return "buy"
    elif "跌" in title or "跌停" in title:
        return "sell"
    return "hold"


def record_signal_from_pipeline(signal: Dict):
    """把 signal_pipeline 产生的信号写入复盘数据库"""
    try:
        code = _extract_code_from_title(signal.get("title", ""))
        if not code:
            return
        price = _extract_price_from_content(signal.get("content", ""))
        sig_type = _infer_signal_type(signal.get("type", ""), signal.get("title", ""))
        reason = signal.get("content", "")[:200]
        
        conn = sqlite3.connect(_FINANCE_DB, timeout=10)
        try:
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS sig_decision_signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    signal_id TEXT UNIQUE,
                    stock_code TEXT NOT NULL,
                    stock_name TEXT,
                    signal_type TEXT NOT NULL,
                    confidence INTEGER DEFAULT 5,
                    reason TEXT,
                    target_price REAL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            now = datetime.now().isoformat()
            signal_id = f"SP-{now.replace(':', '').replace('-', '').replace('.', '')}-{code}"
            
            cursor.execute('''
                INSERT OR IGNORE INTO sig_decision_signals
                (signal_id, stock_code, signal_type, target_price, reason, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (signal_id, code, sig_type, price, reason, now))
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        logger.warning(f"记录信号到复盘库失败: {e}")


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
    try:
        with open(ALERT_LOG, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
    except OSError as e:
        logger.warning(f"保存缓存失败: {e}")


def is_cooled_down(stock_code: str, signal_type: str, cache: Dict) -> bool:
    """检查是否过了冷却期"""
    key = f"{stock_code}:{signal_type}"
    last_sent = cache.get(key, 0)
    
    # 处理字符串时间戳（兼容旧格式）
    if isinstance(last_sent, str):
        try:
            from datetime import datetime
            # 尝试解析 isoformat
            dt = datetime.fromisoformat(last_sent.replace('Z', '+00:00'))
            last_sent = dt.timestamp()
        except (ValueError, AttributeError):
            # 解析失败，当作不存在
            return True
    
    if last_sent == 0 or not isinstance(last_sent, (int, float)):
        return True
    
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
            code = code_part.split("_")[-1]  # e.g. sh600519
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
            
            # 零值过滤：盘前/盘后数据源可能返回无效零值
            if price <= 0 or pre_close <= 0:
                logger.debug(f"跳过零值数据: {code} price={price} pre_close={pre_close}")
                continue
            
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

def check_price_move(stock_code: str, data: Dict, market_panic: bool = False) -> Optional[Dict]:
    """检查个股价格大幅异动（涨幅≥5% + 成交额≥10亿）
    
    参数:
        market_panic: 大盘恐慌模式（跌≥2%），此时收紧推送
    """
    change_pct = data.get("change_pct", 0)
    turnover = data.get("turnover", 0)
    price = data.get("price", 0)
    high = data.get("high", 0)
    low = data.get("low", 0)
    pre_close = data.get("pre_close", 0)
    name = data.get("name", stock_code)
    
    # 防御：pre_close为零时无法计算振幅，跳过
    if pre_close <= 0:
        return None
    
    # 恐慌模式：只推涨停/跌停（由check_limit_up_down处理），这里跳过
    if market_panic and abs(change_pct) < THRESHOLDS["price_change_limit"]:
        return None
    
    # 正常模式：涨幅≥5% 且 成交额≥10亿
    if abs(change_pct) >= THRESHOLDS["price_change_big"] and turnover >= THRESHOLDS["turnover_min"]:
        direction = "📈" if change_pct > 0 else "📉"
        return {
            "type": "volume_anomaly",
            "level": "warning",
            "title": f"{direction} {name}({stock_code}) 异动",
            "content": (
                f"价格: {price:.2f} ({'+' if change_pct > 0 else ''}{change_pct}%)\n"
                f"成交额: {turnover/1e8:.1f}亿\n"
                f"振幅: {(high-low)/pre_close*100:.1f}%"
            ),
        }
    return None


def check_limit_up_down(stock_code: str, data: Dict) -> Optional[Dict]:
    """检查涨停/跌停预警"""
    change_pct = data.get("change_pct", 0)
    pre_close = data.get("pre_close", 0)
    price = data.get("price", 0)
    name = data.get("name", stock_code)
    
    # 防御：pre_close 为零时无法计算涨停价，跳过
    if pre_close <= 0 or price <= 0:
        return None
    
    # 科创板/创业板涨停20%，主板10%
    bare_code = stock_code[2:] if stock_code.startswith(("sh", "sz")) else stock_code
    is_kc_cy = bare_code.startswith("688") or bare_code.startswith("300")
    limit_pct = 20.0 if is_kc_cy else 10.0
    near_limit = limit_pct - 0.5
    
    if change_pct >= near_limit:
        return {
            "type": "limit_up",
            "level": "urgent",
            "title": f"🚀 {name}({stock_code}) 接近涨停！",
            "content": (
                f"当前: {price:.2f} (+{change_pct}%)\n"
                f"涨停价: {pre_close * (1 + limit_pct/100):.2f}\n"
                f"距涨停: {(limit_pct - change_pct):.2f}%"
            ),
        }
    elif change_pct <= -near_limit:
        return {
            "type": "limit_down",
            "level": "urgent",
            "title": f"💥 {name}({stock_code}) 接近跌停！",
            "content": (
                f"当前: {price:.2f} ({change_pct}%)\n"
                f"跌停价: {pre_close * (1 - limit_pct/100):.2f}\n"
                f"距跌停: {abs(limit_pct + change_pct):.2f}%"
            ),
        }
    return None


def check_index_anomaly(index_code: str, data: Dict) -> Optional[Dict]:
    """检查大盘异常波动"""
    change_pct = data.get("change_pct", 0)
    price = data.get("price", 0)
    high = data.get("high", 0)
    low = data.get("low", 0)
    pre_close = data.get("pre_close", 0)
    name = INDEX_CODES.get(index_code, index_code)
    
    # 防御：pre_close为零时无法计算振幅，跳过
    if pre_close <= 0:
        return None
    
    if abs(change_pct) >= THRESHOLDS["index_change"]:
        direction = "📈" if change_pct > 0 else "📉"
        return {
            "type": "index_anomaly",
            "level": "warning",
            "title": f"{direction} {name} 大幅波动 {change_pct}%",
            "content": (
                f"当前: {price:.2f}\n"
                f"涨跌: {'+' if change_pct > 0 else ''}{change_pct}%\n"
                f"振幅: {(high-low)/pre_close*100:.1f}%"
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
    title = signal.get("title", "")
    content = signal.get("content", "")
    return f"{emoji} {title}\n\n{content}"


def queue_alert(msg: str) -> bool:
    """推送告警到消息队列（由 cron 读取并发送到当前对话）"""
    import fcntl
    
    # 防御：空消息不写入队列
    if not msg or not msg.strip():
        return False
    
    pending_file = os.path.join(WORKSPACE, ".alerts", "pending_alerts.json")
    
    try:
        os.makedirs(os.path.dirname(pending_file), exist_ok=True)
        
        # 使用文件锁防止竞态条件
        with open(pending_file, "a+") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                f.seek(0)
                content = f.read()
                alerts = json.loads(content) if content.strip() else []
            except json.JSONDecodeError:
                alerts = []
            
            alerts.append({
                "time": datetime.now(timezone(timedelta(hours=8))).isoformat(),
                "msg": msg
            })
            
            f.seek(0)
            f.truncate()
            json.dump(alerts, f, ensure_ascii=False, indent=2)
            f.flush()
            fcntl.flock(f, fcntl.LOCK_UN)
        
        return True
    except Exception as e:
        logger.error(f"写入推送队列失败: {e}")
        return False


# ========== 主流程 ==========

def run_realtime_scan():
    """实时扫描（交易时段）"""
    now = datetime.now(timezone(timedelta(hours=8)))
    h, m = now.hour, now.minute
    
    # 严格交易时间校验：A股 09:30-11:30, 13:00-15:00（15:00是收盘最后一分钟）
    in_trading = (
        (h == 9 and m >= 30) or
        (h == 10) or
        (h == 11 and m <= 30) or
        (h == 13) or
        (h == 14) or
        (h == 15 and m == 0)
    )
    if not in_trading:
        # 非交易时段静默退出，不输出任何内容（避免cron推送噪音）
        return []
    
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
    
    # 零值数据防护：如果多数票返回零值，标记为数据源异常
    zero_count = sum(1 for d in stock_quotes.values() if d["price"] <= 0)
    if zero_count > len(stock_quotes) * 0.5:
        logger.warning(f"⚠️ 数据源异常: {zero_count}/{len(stock_quotes)} 只股票价格为零，跳过扫描")
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
        signal = check_price_move(code, data, market_panic=market_panic)
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
        record_signal_from_pipeline(sig)
        if queue_alert(msg):
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
    
    def _is_kc_cy(code: str) -> bool:
        bare = code[2:] if code.startswith(("sh", "sz")) else code
        return bare.startswith("688") or bare.startswith("300")
    
    # 统计（使用 .get() 防止 KeyError，按板块区分涨停阈值）
    up_count = sum(1 for d in stock_quotes.values() if d.get("change_pct", 0) > 0)
    down_count = sum(1 for d in stock_quotes.values() if d.get("change_pct", 0) < 0)
    limit_up = [c for c, d in stock_quotes.items() if d.get("change_pct", 0) >= (19.5 if _is_kc_cy(c) else 9.5)]
    limit_down = [c for c, d in stock_quotes.items() if d.get("change_pct", 0) <= -(19.5 if _is_kc_cy(c) else 9.5)]
    
    summary = (
        f"📊 今日复盘 ({now.strftime('%m-%d')})\n"
        f"上涨: {up_count}只 | 下跌: {down_count}只\n"
        f"涨停: {len(limit_up)}只 | 跌停: {len(limit_down)}只\n"
    )
    if limit_up:
        names = [stock_quotes[c].get("name", c) for c in limit_up[:5]]
        summary += f"🔥 涨停: {', '.join(names)}{'...' if len(limit_up) > 5 else ''}\n"
    if limit_down:
        names = [stock_quotes[c].get("name", c) for c in limit_down[:5]]
        summary += f"💥 跌停: {', '.join(names)}{'...' if len(limit_down) > 5 else ''}\n"
    
    logger.info(f"\n{summary}")
    if queue_alert(summary):
        logger.info("日终总结已推送")
    else:
        logger.warning("日终总结推送失败")
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
