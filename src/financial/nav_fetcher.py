#!/usr/bin/env python3
"""
161130 净值抓取器
来源：天天基金网 (fund.eastmoney.com)
编码：GBK
"""

import requests
import re
from datetime import datetime

FUND_CODE = "161130"
FUND_URL = f"https://fund.eastmoney.com/{FUND_CODE}.html"


def fetch_nav():
    """抓取 161130 最新净值和估算净值"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Referer": "https://fund.eastmoney.com/"
    }
    
    try:
        r = requests.get(FUND_URL, headers=headers, timeout=20)
        r.raise_for_status()
        
        # GBK 解码
        html = r.content.decode("gbk", errors="replace")
        
        result = {
            "fund_code": FUND_CODE,
            "fetched_at": datetime.now().isoformat(),
            "source": "eastmoney",
            "nav": None,
            "nav_date": None,
            "estimated_nav": None,
            "premium_pct": None,
        }
        
        # 提取单位净值 (fix_dwjz 类)
        nav_match = re.search(r'class="fix_dwjz[^"]*">([0-9]\.[0-9]{4})', html)
        if nav_match:
            result["nav"] = float(nav_match.group(1))
        
        # 提取净值日期 (fix_date 类，格式如 "(09-10)")
        date_match = re.search(r'class="fix_date">\(([0-9]{2}-[0-9]{2})\)', html)
        if date_match:
            mm_dd = date_match.group(1)
            year = datetime.now().year
            result["nav_date"] = f"{year}-{mm_dd}"
        
        # 提取涨跌幅 (fix_zzl 类)
        pct_match = re.search(r'class="fix_zzl[^"]*">([+-]?[0-9]+\.[0-9]{2})%', html)
        if pct_match:
            result["nav_change_pct"] = float(pct_match.group(1))
        
        # 提取溢价率 (如果页面有)
        premium_match = re.search(r'溢价率[：:]\s*<[^>]*>([+-]?[0-9]+\.[0-9]+)', html)
        if premium_match:
            result["premium_pct"] = float(premium_match.group(1))
        
        return result
        
    except Exception as e:
        return {
            "fund_code": FUND_CODE,
            "fetched_at": datetime.now().isoformat(),
            "source": "eastmoney",
            "error": str(e),
        }


def fetch_fund_price(fund_code: str = FUND_CODE) -> dict:
    """
    抓取 LOF 场内行情（新浪财经）

    用于计算「盘前参考偏离率」的前收盘价。
    新浪字段顺序: 名称,今开,昨收,最新价,最高,最低,...,日期,时间

    Returns:
        {"latest": 最新价, "prev_close": 昨收, "date": 行情日期, "source": "sina"}
        失败时返回 {"error": ...}
    """
    symbol = f"sz{fund_code}"
    url = f"https://hq.sinajs.cn/list={symbol}"

    try:
        r = requests.get(
            url,
            headers={"Referer": "https://finance.sina.com.cn"},
            timeout=10,
        )
        r.raise_for_status()
        r.encoding = "gbk"

        payload = r.text.split('"')[1] if '"' in r.text else ""
        parts = payload.split(",")
        if len(parts) < 4:
            raise ValueError(f"字段不足 ({len(parts)})")

        def _num(x):
            try:
                v = float(x)
                return v if v > 0 else None
            except (TypeError, ValueError):
                return None

        return {
            "fund_code": fund_code,
            "name": parts[0],
            "open": _num(parts[1]),
            "prev_close": _num(parts[2]),
            "latest": _num(parts[3]),
            "high": _num(parts[4]) if len(parts) > 4 else None,
            "low": _num(parts[5]) if len(parts) > 5 else None,
            "date": parts[30] if len(parts) > 30 else None,
            "time": parts[31] if len(parts) > 31 else None,
            "source": "sina",
        }
    except Exception as e:
        return {"fund_code": fund_code, "source": "sina", "error": str(e)}


if __name__ == "__main__":
    data = fetch_nav()
    print(f"基金代码: {data['fund_code']}")
    print(f"最新净值: {data.get('nav')}")
    print(f"净值日期: {data.get('nav_date')}")
    print(f"估算净值: {data.get('estimated_nav')}")
    print(f"溢价率: {data.get('premium_pct')}%")
    if data.get("error"):
        print(f"错误: {data['error']}")

    print("\n--- 场内行情 ---")
    price = fetch_fund_price()
    if price.get("error"):
        print(f"抓取失败: {price['error']}")
    else:
        print(f"名称: {price['name']}")
        print(f"最新价: {price['latest']} (昨收 {price['prev_close']})")
        print(f"行情日期: {price['date']} {price['time']}")
