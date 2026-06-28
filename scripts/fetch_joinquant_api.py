#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
聚宽模拟交易日志API抓取（直接调API，无需浏览器）
"""
import json
import os
import re
import sqlite3
import sys
from datetime import datetime

try:
    import requests
except ImportError:
    print("错误：未安装 requests")
    print("  pip3 install requests")
    sys.exit(1)

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "finance.db")

# Cookie数据
COOKIES = {
    "uid": "wKgyrWohTYl46gW0mPsjAg==",
    "token": "4534895bbc416603f7aa6bf5c6d96fc4e3b11b26",
    "PHPSESSID": "tb0o5l0ldkjhkuoa7t07hc8t70",
    "tips": "1",
    "getStrategy": "1",
    "isFirst": "0",
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Referer": "https://www.joinquant.com/algorithm/live/index",
}


def fetch_logs(backtest_id):
    """获取策略日志"""
    url = f"https://www.joinquant.com/algorithm/backtest/export?type=log&backtestId={backtest_id}"
    
    resp = requests.get(url, headers=HEADERS, cookies=COOKIES, timeout=30)
    
    if resp.status_code != 200:
        print(f"请求失败: {resp.status_code}")
        return None
    
    # 尝试多种编码解码
    content = None
    for encoding in ["utf-8", "gbk", "gb2312", "latin-1"]:
        try:
            content = resp.content.decode(encoding)
            print(f"使用编码: {encoding}")
            break
        except:
            continue
    
    if not content:
        print("无法解码响应")
        return None
    
    return content


def parse_trades_from_logs(log_text):
    """从日志文本中解析交易记录"""
    trades = []
    lines = log_text.split("\n")
    
    # 匹配模式：调仓下单/ETF底仓调整/止盈减仓等
    patterns = [
        # 调仓下单 买入 中际旭创(300308.XSHE) 当前=0 目标=100 变化=100 状态=试仓 仓位系数=1.00
        r'(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2}:\d{2}).*?([\u4e00-\u9fa5]+).*?([\u4e00-\u9fa5]+)\s+([\u4e00-\u9fa5]+)\((\d{6})\.(XSHG|XSHE)\)\s+.*?当前=(\d+)\s+目标=(\d+)\s+变化=([\-\d]+)',
        # ETF底仓调整
        r'(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2}:\d{2}).*?ETF.*?([\u4e00-\u9fa5]+)\((\d{6})\.(XSHG|XSHE)\)\s+.*?当前=(\d+)\s+目标=(\d+)\s+变化=([\-\d]+)',
    ]
    
    for line in lines:
        # 尝试匹配各种模式
        # 简单匹配：日期时间 + 股票代码 + 买卖 + 数量变化
        
        # 提取日期时间
        dt_match = re.match(r'(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2}:\d{2})', line)
        if not dt_match:
            continue
        
        date_str = dt_match.group(1)
        time_str = dt_match.group(2)
        
        # 提取股票代码
        code_match = re.search(r'(\d{6})\.(XSHG|XSHE)', line)
        if not code_match:
            continue
        
        code = code_match.group(1)
        exchange = code_match.group(2)
        
        # 判断方向：买入/卖出/调整
        side = "adjust"
        if "买入" in line and "卖出" not in line:
            side = "buy"
        elif "卖出" in line and "买入" not in line:
            side = "sell"
        elif "变化=" in line:
            # 从变化量判断
            change_match = re.search(r'变化=([\-\d]+)', line)
            if change_match:
                change = int(change_match.group(1))
                if change > 0:
                    side = "buy"
                elif change < 0:
                    side = "sell"
        
        # 提取数量
        qty_match = re.search(r'目标=(\d+)', line)
        quantity = int(qty_match.group(1)) if qty_match else 0
        
        # 提取当前数量
        cur_match = re.search(r'当前=(\d+)', line)
        current = int(cur_match.group(1)) if cur_match else 0
        
        # 变化量
        delta = quantity - current
        
        trades.append({
            "date": date_str,
            "time": time_str,
            "code": code,
            "exchange": exchange,
            "side": side,
            "quantity": quantity,
            "delta": delta,
            "raw": line.strip(),
        })
    
    return trades


def main():
    backtest_id = os.environ.get("JQ_BACKTEST_ID", "054e0e6887a2e77a64468e68d8419535")
    
    print(f"正在获取日志: {backtest_id}")
    log_text = fetch_logs(backtest_id)
    
    if not log_text:
        print("获取失败")
        return 1
    
    print(f"\n日志长度: {len(log_text)} 字符")
    print(f"日志行数: {len(log_text.split(chr(10)))}")
    
    # 保存原始日志
    with open("/tmp/jq_logs_decoded.txt", "w", encoding="utf-8") as f:
        f.write(log_text)
    print("原始日志已保存: /tmp/jq_logs_decoded.txt")
    
    # 解析交易
    trades = parse_trades_from_logs(log_text)
    print(f"\n解析到 {len(trades)} 条交易记录")
    
    # 显示前20条
    for t in trades[:20]:
        print(f"  {t['date']} {t['time']} {t['code']} {t['side']:6s} 目标={t['quantity']:5d} 变化={t['delta']:+6d}")
    
    # 保存到数据库
    if trades:
        print("\n正在导入数据库...")
        db_path = os.path.abspath(DB_PATH)
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        imported = 0
        for t in trades:
            try:
                symbol = f"{t['code']}.SH" if t['exchange'] == 'XSHG' else f"{t['code']}.SZ"
                if t['delta'] == 0:
                    continue
                
                side = "buy" if t['delta'] > 0 else "sell"
                qty = abs(t['delta'])
                
                cursor.execute("""
                    INSERT INTO portfolio_events
                    (event_type, event_date, symbol, side, quantity, price, note)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, ("trade", t['date'], symbol, side, qty, 0, t['raw'][:100]))
                imported += 1
            except Exception as e:
                print(f"导入失败: {e}")
        
        conn.commit()
        conn.close()
        print(f"成功导入 {imported} 条交易记录")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
