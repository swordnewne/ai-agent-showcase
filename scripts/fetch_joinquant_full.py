#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
聚宽模拟交易数据API抓取（日志+持仓）
直接调用聚宽API，无需浏览器登录
"""
import csv
import io
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


def fetch_api(backtest_id, data_type):
    """获取API数据"""
    url = f"https://www.joinquant.com/algorithm/backtest/export?type={data_type}&backtestId={backtest_id}"
    
    resp = requests.get(url, headers=HEADERS, cookies=COOKIES, timeout=30)
    
    if resp.status_code != 200:
        print(f"请求失败: {resp.status_code}")
        return None
    
    # 尝试多种编码解码
    for encoding in ["gbk", "gb2312", "utf-8", "latin-1"]:
        try:
            return resp.content.decode(encoding)
        except:
            continue
    
    return None


def parse_logs(log_text):
    """解析日志中的交易记录"""
    trades = []
    lines = log_text.split("\n")
    
    for line in lines:
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
        
        # 判断方向
        side = "adjust"
        if "买入" in line and "卖出" not in line:
            side = "buy"
        elif "卖出" in line and "买入" not in line:
            side = "sell"
        
        # 提取数量变化
        delta = 0
        change_match = re.search(r'变化=([\-\d]+)', line)
        if change_match:
            delta = int(change_match.group(1))
        
        if delta == 0:
            continue
        
        # 确定最终方向
        side = "buy" if delta > 0 else "sell"
        qty = abs(delta)
        
        trades.append({
            "date": date_str,
            "time": time_str,
            "code": code,
            "exchange": exchange,
            "side": side,
            "quantity": qty,
            "raw": line.strip(),
        })
    
    return trades


def parse_positions(csv_text):
    """解析持仓CSV"""
    positions = []
    
    reader = csv.DictReader(io.StringIO(csv_text))
    
    for row in reader:
        try:
            date = row.get('日期', '').strip()
            if not date or date == '日期':
                continue
            
            # 从"标的"列提取代码，格式: 300ETF(510300.XSHG)
            code_raw = row.get('标的', '')
            code_match = re.search(r'(\d{6})\.(XSHG|XSHE)', code_raw)
            if not code_match:
                continue
            
            code = code_match.group(1)
            exchange = code_match.group(2)
            
            # 提取数量
            qty_str = row.get('数量', '')
            qty_match = re.search(r'(\d+)', str(qty_str))
            qty = int(qty_match.group(1)) if qty_match else 0
            
            if qty <= 0:
                continue
            
            # 提取成本价（开仓均价）
            cost_str = row.get('开仓均价', '')
            try:
                cost = float(str(cost_str).replace(',', ''))
            except:
                cost = 0
            
            positions.append({
                "date": date,
                "code": code,
                "exchange": exchange,
                "quantity": qty,
                "cost": cost,
                "raw": str(row),
            })
        except Exception as e:
            continue
    
    return positions


def save_to_db(trades, positions):
    """保存到数据库"""
    db_path = os.path.abspath(DB_PATH)
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # 创建表（如果不存在）
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS portfolio_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            event_date TEXT NOT NULL,
            event_time TEXT,
            symbol TEXT NOT NULL,
            side TEXT,
            quantity INTEGER,
            price REAL,
            note TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # 保存交易记录（去重：同一日期时间同一股票同一方向）
    trade_count = 0
    for t in trades:
        try:
            symbol = f"{t['code']}.SH" if t['exchange'] == 'XSHG' else f"{t['code']}.SZ"
            # 检查是否已存在
            cursor.execute("""
                SELECT id FROM portfolio_events 
                WHERE event_type = 'trade' AND event_date = ? AND symbol = ? AND side = ? AND quantity = ?
            """, (t['date'], symbol, t['side'], t['quantity']))
            if cursor.fetchone():
                continue  # 已存在，跳过
            
            cursor.execute("""
                INSERT INTO portfolio_events
                (event_type, event_date, symbol, side, quantity, price, note)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, ("trade", t['date'], symbol, t['side'], t['quantity'], 0, f"{t['time']} {t['raw'][:80]}"))
            trade_count += 1
        except Exception as e:
            print(f"交易导入失败: {e}")
    
    # 保存持仓快照（所有日期保留，同一日期同一股票去重）
    pos_count = 0
    if positions:
        for p in positions:
            try:
                symbol = f"{p['code']}.SH" if p['exchange'] == 'XSHG' else f"{p['code']}.SZ"
                # 先检查是否已存在
                cursor.execute("""
                    SELECT id FROM portfolio_events 
                    WHERE event_type = 'position' AND event_date = ? AND symbol = ?
                """, (p['date'], symbol))
                if cursor.fetchone():
                    continue  # 已存在，跳过
                
                cursor.execute("""
                    INSERT INTO portfolio_events
                    (event_type, event_date, symbol, side, quantity, price, note)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, ("position", p['date'], symbol, "hold", p['quantity'], p['cost'], f"持仓快照 {p['date']}"))
                pos_count += 1
            except Exception as e:
                print(f"持仓导入失败: {e}")
    
    conn.commit()
    conn.close()
    
    return trade_count, pos_count


def main():
    backtest_id = os.environ.get("JQ_BACKTEST_ID", "054e0e6887a2e77a64468e68d8419535")
    
    print(f"=== 聚宽数据抓取 ===")
    print(f"Backtest ID: {backtest_id}\n")
    
    # 1. 获取日志
    print("1. 获取交易日志...")
    log_text = fetch_api(backtest_id, "log")
    if log_text:
        with open("/tmp/jq_logs.txt", "w", encoding="utf-8") as f:
            f.write(log_text)
        trades = parse_logs(log_text)
        print(f"   解析到 {len(trades)} 条交易记录")
    else:
        trades = []
        print("   获取失败")
    
    # 2. 获取持仓
    print("\n2. 获取持仓快照...")
    pos_text = fetch_api(backtest_id, "position")
    if pos_text:
        with open("/tmp/jq_positions.csv", "w", encoding="utf-8") as f:
            f.write(pos_text)
        positions = parse_positions(pos_text)
        print(f"   解析到 {len(positions)} 条持仓记录")
    else:
        positions = []
        print("   获取失败")
    
    # 3. 显示最新持仓
    if positions:
        print("\n=== 最新持仓 ===")
        # 按日期分组显示
        dates = sorted(set(p['date'] for p in positions))
        if dates:
            latest = dates[-1]
            latest_pos = [p for p in positions if p['date'] == latest]
            for p in latest_pos[:10]:
                symbol = f"{p['code']}.{p['exchange']}"
                print(f"  {symbol:15s} 数量={p['quantity']:6d} 成本={p['cost']:.2f}")
            if len(latest_pos) > 10:
                print(f"  ... 共 {len(latest_pos)} 只")
    
    # 4. 显示最近交易
    if trades:
        print("\n=== 最近交易 ===")
        for t in trades[-10:]:
            symbol = f"{t['code']}.{t['exchange']}"
            print(f"  {t['date']} {t['time']} {symbol:15s} {t['side']:6s} {t['quantity']:6d}股")
    
    # 5. 保存到数据库
    print("\n3. 保存到数据库...")
    trade_count, pos_count = save_to_db(trades, positions)
    print(f"   交易记录: {trade_count} 条")
    print(f"   持仓快照: {pos_count} 条")
    
    print("\n✅ 完成！")
    print(f"   日志文件: /tmp/jq_logs.txt")
    print(f"   持仓文件: /tmp/jq_positions.csv")
    print(f"   数据库: {DB_PATH}")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
