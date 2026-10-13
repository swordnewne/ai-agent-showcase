#!/usr/bin/env python3
"""
美股行情数据抓取器
来源：新浪财经API（国内可访问）
替代：yfinance（国内429限速不可用）

API格式：
- 纳指期货: hq.sinajs.cn/list=hf_NQ
- 美股个股: hq.sinajs.cn/list=gb_<代码>
- 外汇: hq.sinajs.cn/list=fx_susdcny
"""

import requests
import re
import math
from datetime import datetime

SINA_HQ = "https://hq.sinajs.cn"

# 美股七巨头代码映射
US_STOCKS = {
    "AAPL": "gb_aapl",
    "MSFT": "gb_msft",
    "NVDA": "gb_nvda",
    "TSLA": "gb_tsla",
    "GOOGL": "gb_goog",  # 谷歌A类股
    "AMZN": "gb_amzn",
    "META": "gb_meta",
}


def _parse_sina_response(text: str) -> dict:
    """解析新浪行情返回的JS格式"""
    # 格式: var hq_str_hf_NQ="数据";
    match = re.search(r'="([^"]*)"', text)
    if not match:
        return None
    return match.group(1).split(",")


def _number(parts, index):
    """Return a finite numeric field, or None for an empty/malformed value."""
    try:
        value = float(parts[index])
        return value if math.isfinite(value) else None
    except (IndexError, TypeError, ValueError):
        return None


def _validate_ohlc(data: dict) -> dict:
    """Reject impossible quotes instead of silently publishing shifted fields."""
    required = (data.get("latest"), data.get("prev_close"), data.get("open"),
                data.get("high"), data.get("low"))
    if any(value is None or value <= 0 for value in required):
        return {"error": "行情字段缺失或非正数", "symbol": data.get("symbol"),
                "source": "sina"}
    if data["high"] < max(data["open"], data["latest"]) or data["low"] > min(data["open"], data["latest"]):
        return {"error": "行情高低价不满足基本不变量", "symbol": data.get("symbol"),
                "source": "sina"}
    return data


def fetch_nasdaq_futures() -> dict:
    """抓取纳指期货（替代纳指100现货）"""
    try:
        url = f"{SINA_HQ}/list=hf_NQ"
        r = requests.get(url, timeout=10, headers={"Referer": "https://finance.sina.com.cn"})
        r.raise_for_status()
        
        parts = _parse_sina_response(r.text)
        if not parts or len(parts) < 14:
            return {"error": "数据格式异常", "raw": r.text[:200]}
        
        # 纳指期货字段: 最新, _, 昨收, 今开, 最高, 最低, 时间, _, _, _, _, _, 日期, 名称
        return _validate_ohlc({
            "symbol": "hf_NQ",
            "name": "纳斯达克指数期货",
            "latest": _number(parts, 0),
            "prev_close": _number(parts, 2),
            "open": _number(parts, 3),
            "high": _number(parts, 4),
            "low": _number(parts, 5),
            "time": parts[6] if len(parts) > 6 else None,
            "date": parts[12] if len(parts) > 12 else None,
            "fetched_at": datetime.now().isoformat(),
            "source": "sina",
        })
    except Exception as e:
        return {"error": str(e), "symbol": "hf_NQ"}


def fetch_us_stock(symbol: str) -> dict:
    """抓取美股个股"""
    sina_code = US_STOCKS.get(symbol)
    if not sina_code:
        return {"error": f"不支持的代码: {symbol}"}
    
    try:
        url = f"{SINA_HQ}/list={sina_code}"
        r = requests.get(url, timeout=10, headers={"Referer": "https://finance.sina.com.cn"})
        r.raise_for_status()
        
        parts = _parse_sina_response(r.text)
        if not parts or len(parts) < 3:
            return {"error": "数据格式异常", "raw": r.text[:200]}
        
        # 美股字段: 名称, 最新价, 涨跌幅(%), 时间, 涨跌额, 开盘价, 昨收, 最高, 最低, 成交量, ...
        return _validate_ohlc({
            "symbol": symbol,
            "name": parts[0],
            "latest": _number(parts, 1),
            "change_pct": _number(parts, 2),
            "time": parts[3] if len(parts) > 3 else None,
            # Observed gb_* layout: name,last,change%,time,change_amt,open,high,low,52w_high,...
            # The previous close is the later reference quote at index 26 (last - change_amt).
            "prev_close": _number(parts, 26),
            "open": _number(parts, 5),
            "high": _number(parts, 6),
            "low": _number(parts, 7),
            "fetched_at": datetime.now().isoformat(),
            "source": "sina",
        })
    except Exception as e:
        return {"error": str(e), "symbol": symbol}


def fetch_usd_cny() -> dict:
    """抓取美元兑人民币汇率。

    fx_susdcny 的实测布局是 time,bid,latest,spread,...,ask,high,low；
    不再把无意义的第 5 列当作买价。
    """
    try:
        url = f"{SINA_HQ}/list=fx_susdcny"
        r = requests.get(url, timeout=10, headers={"Referer": "https://finance.sina.com.cn"})
        r.raise_for_status()
        parts = _parse_sina_response(r.text)
        if not parts or len(parts) < 8:
            return {"error": "数据格式异常", "raw": r.text[:200]}
        latest = _number(parts, 2)
        if latest is None or latest <= 0:
            return {"error": "汇率最新价缺失或异常", "symbol": "USD/CNY", "source": "sina"}
        return {
            "symbol": "USD/CNY", "name": "美元兑人民币", "latest": latest,
            "bid": _number(parts, 1), "ask": _number(parts, 5),
            "high": _number(parts, 6), "low": _number(parts, 7),
            "time": parts[0], "fetched_at": datetime.now().isoformat(), "source": "sina",
        }
    except Exception as e:
        return {"error": str(e), "symbol": "USD/CNY"}

def fetch_dow_futures() -> dict:
    """抓取道指期货"""
    try:
        url = f"{SINA_HQ}/list=hf_YM"  # 道指期货代码是 hf_YM
        r = requests.get(url, timeout=10, headers={"Referer": "https://finance.sina.com.cn"})
        r.raise_for_status()
        parts = _parse_sina_response(r.text)
        if not parts or len(parts) < 14:
            return {"error": "数据格式异常", "raw": r.text[:100]}
        return {
            "symbol": "hf_YM",
            "name": "道琼斯指数期货",
            "latest": _number(parts, 0),
            "prev_close": _number(parts, 2),
            "fetched_at": datetime.now().isoformat(),
            "source": "sina",
        }
    except Exception as e:
        return {"error": str(e), "symbol": "hf_YM"}


def fetch_sp500_futures() -> dict:
    """抓取标普500期货"""
    try:
        url = f"{SINA_HQ}/list=hf_ES"  # 标普期货代码是 hf_ES
        r = requests.get(url, timeout=10, headers={"Referer": "https://finance.sina.com.cn"})
        r.raise_for_status()
        parts = _parse_sina_response(r.text)
        if not parts or len(parts) < 14:
            return {"error": "数据格式异常", "raw": r.text[:100]}
        return {
            "symbol": "hf_ES",
            "name": "标普500指数期货",
            "latest": float(parts[0]) if parts[0] else None,
            "prev_close": float(parts[2]) if parts[2] else None,
            "fetched_at": datetime.now().isoformat(),
            "source": "sina",
        }
    except Exception as e:
        return {"error": str(e), "symbol": "hf_ES"}


def fetch_a_share_index(index_code: str, name: str) -> dict:
    """抓取A股指数"""
    try:
        url = f"{SINA_HQ}/list={index_code}"
        r = requests.get(url, timeout=10, headers={"Referer": "https://finance.sina.com.cn"})
        r.raise_for_status()
        parts = _parse_sina_response(r.text)
        if not parts or len(parts) < 5:
            return {"error": "数据格式异常"}
        # A股指数格式: 名称, 今日开盘, 昨日收盘, 最新价, 最高, 最低, ...
        return {
            "symbol": index_code,
            "name": name,
            "open": float(parts[1]) if parts[1] else None,
            "prev_close": float(parts[2]) if parts[2] else None,
            "latest": float(parts[3]) if parts[3] else None,
            "high": _number(parts, 4),
            "low": _number(parts, 5),
            "fetched_at": datetime.now().isoformat(),
            "source": "sina",
        }
    except Exception as e:
        return {"error": str(e), "symbol": index_code}


def fetch_all_market_data() -> dict:
    """抓取所有市场数据"""
    result = {
        "nasdaq": fetch_nasdaq_futures(),
        "dow": fetch_dow_futures(),
        "sp500": fetch_sp500_futures(),
        "usd_cny": fetch_usd_cny(),
        "a_share": {
            "shanghai": fetch_a_share_index("sh000001", "上证指数"),
            "shenzhen": fetch_a_share_index("sz399001", "深证成指"),
        },
        "stocks": {},
        "fetched_at": datetime.now().isoformat(),
    }
    
    # 抓取七巨头
    for symbol in US_STOCKS:
        result["stocks"][symbol] = fetch_us_stock(symbol)
    
    return result


if __name__ == "__main__":
    data = fetch_all_market_data()
    
    print("=== 纳指期货 ===")
    nq = data["nasdaq"]
    if "error" in nq:
        print(f"错误: {nq['error']}")
    else:
        print(f"最新: {nq.get('latest')}")
        print(f"昨收: {nq.get('prev_close')}")
        print(f"日期: {nq.get('date')}")
    
    print("\n=== 汇率 ===")
    fx = data["usd_cny"]
    if "error" in fx:
        print(f"错误: {fx['error']}")
    else:
        print(f"USD/CNY: {fx.get('latest')}")
    
    print("\n=== 美股个股 ===")
    for sym, info in data["stocks"].items():
        if "error" in info:
            print(f"{sym}: 错误 - {info['error']}")
        else:
            print(f"{sym}: {info.get('latest')} ({info.get('change_pct'):+.2f}%)")
