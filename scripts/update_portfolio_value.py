#!/usr/bin/env python3
"""
聚宽模拟盘 每日市值更新
用法: 
  1. 先获取实时价格: python3 fetch_prices.py
  2. 再计算市值: python3 update_portfolio_value.py
"""
import sqlite3
import csv
import os
import sys
import json
import re
from datetime import datetime, timezone

DB_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', 'finance.db')
PRICE_CACHE = os.path.join(os.path.dirname(__file__), '..', 'data', 'prices.csv')

NAME_MAP = {
    '002008.SZ': '大族激光', '002475.SZ': '立讯精密', '159915.SZ': '创业板ETF',
    '300223.SZ': '北京君正', '300308.SZ': '中际旭创', '300433.SZ': '蓝思科技',
    '300502.SZ': '新易盛', '300661.SZ': '圣邦股份', '510300.SH': '300ETF',
    '510500.SH': '500ETF', '688256.SH': '寒武纪', '688390.SH': '固德威',
}


def ensure_table(conn):
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS portfolio_value (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            total_cost REAL DEFAULT 0,
            total_value REAL DEFAULT 0,
            total_pnl REAL DEFAULT 0,
            total_pnl_pct REAL DEFAULT 0,
            holdings_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(date)
        )
    ''')
    conn.commit()


def read_prices():
    """读取价格缓存文件"""
    prices = {}
    if not os.path.exists(PRICE_CACHE):
        return prices
    
    try:
        with open(PRICE_CACHE, 'r') as f:
            reader = csv.DictReader(f)
            for row in reader:
                code = row.get('ts_code', '').strip()
                close = row.get('close', '0').strip()
                if code and close:
                    try:
                        prices[code] = float(close)
                    except:
                        pass
    except Exception as e:
        print(f"读取价格缓存失败: {e}")
    
    return prices


def get_positions(conn):
    cursor = conn.cursor()
    cursor.execute('''
        SELECT event_date, symbol, quantity, price 
        FROM portfolio_events 
        WHERE event_type = 'position'
        ORDER BY event_date DESC, symbol
    ''')
    rows = cursor.fetchall()
    if not rows:
        return []
    latest_date = rows[0][0]
    return [r for r in rows if r[0] == latest_date]


def calculate(positions, prices):
    total_cost = 0
    total_value = 0
    holdings = []
    
    for date, symbol, qty, cost in positions:
        qty = float(qty) if qty else 0
        cost = float(cost) if cost else 0
        current_price = prices.get(symbol, cost)
        
        cost_value = qty * cost
        current_value = qty * current_price
        pnl = current_value - cost_value
        pnl_pct = ((current_price - cost) / cost * 100) if cost else 0
        
        total_cost += cost_value
        total_value += current_value
        
        holdings.append({
            'symbol': symbol,
            'name': NAME_MAP.get(symbol, symbol),
            'quantity': qty,
            'cost_price': cost,
            'current_price': current_price,
            'cost_value': cost_value,
            'current_value': current_value,
            'pnl': pnl,
            'pnl_pct': pnl_pct,
        })
    
    return {
        'date': date,
        'total_cost': total_cost,
        'total_value': total_value,
        'total_pnl': total_value - total_cost,
        'total_pnl_pct': ((total_value - total_cost) / total_cost * 100) if total_cost else 0,
        'holdings': holdings,
    }


def save_result(conn, result):
    cursor = conn.cursor()
    cursor.execute('SELECT id FROM portfolio_value WHERE date = ?', (result['date'],))
    
    if cursor.fetchone():
        cursor.execute('''
            UPDATE portfolio_value SET
                total_cost = ?, total_value = ?, total_pnl = ?, total_pnl_pct = ?,
                holdings_json = ?
            WHERE date = ?
        ''', (result['total_cost'], result['total_value'], result['total_pnl'], result['total_pnl_pct'],
              json.dumps(result['holdings'], ensure_ascii=False),
              result['date']))
    else:
        cursor.execute('''
            INSERT INTO portfolio_value (date, total_cost, total_value, total_pnl, total_pnl_pct, holdings_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (result['date'], result['total_cost'], result['total_value'], result['total_pnl'],
              result['total_pnl_pct'], json.dumps(result['holdings'], ensure_ascii=False),
              datetime.now(timezone.utc).isoformat()))
    conn.commit()


def print_result(result):
    print(f'=== 聚宽模拟盘市值更新 ({result["date"]}) ===')
    print(f'')
    print(f'{"代码":<15} {"名称":<10} {"数量":>8} {"成本价":>10} {"现价":>10} {"成本市值":>12} {"当前市值":>12} {"盈亏":>12}')
    print('-' * 105)
    
    for h in result['holdings']:
        pnl_str = f"{h['pnl']:+.0f} ({h['pnl_pct']:+.1f}%)"
        print(f"{h['symbol']:<15} {h['name']:<10} {h['quantity']:>8.0f} {h['cost_price']:>10.2f} {h['current_price']:>10.2f} {h['cost_value']:>12,.0f} {h['current_value']:>12,.0f} {pnl_str:>12}")
    
    print('-' * 105)
    print(f'{"合计":>40} {result["total_cost"]:>12,.0f} {result["total_value"]:>12,.0f} {result["total_pnl"]:+,.0f} ({result["total_pnl_pct"]:+.1f}%)')
    print(f'')
    print(f'资产概览:')
    print(f'  持仓成本: {result["total_cost"]:,.0f}')
    print(f'  持仓市值: {result["total_value"]:,.0f}')
    print(f'  浮动盈亏: {result["total_pnl"]:+,.0f} ({result["total_pnl_pct"]:+.1f}%)')
    
    # 估算仓位
    total_asset = 1008447  # 聚宽总资产
    cash = total_asset - result['total_value']
    print(f'  估算现金: {cash:,.0f}')
    print(f'  仓位比例: {result["total_value"] / total_asset * 100:.1f}%')


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--date', type=str, help='指定日期 (YYYY-MM-DD)')
    args = parser.parse_args()
    
    # 读取价格
    prices = read_prices()
    if not prices:
        print(f"错误: 价格缓存文件不存在: {PRICE_CACHE}")
        print(f"请先获取实时价格:")
        print(f"  1. 用 kimi_finance 获取: --ticker 代码列表 --type realtime_price --file_path {PRICE_CACHE}")
        print(f"  2. 或手动创建 CSV 文件: ts_code,close")
        return 1
    
    # 连接数据库
    conn = sqlite3.connect(DB_PATH)
    ensure_table(conn)
    
    # 获取持仓
    positions = get_positions(conn)
    if not positions:
        print("没有找到持仓数据")
        return 1
    
    # 计算
    result = calculate(positions, prices)
    if args.date:
        result['date'] = args.date
    
    # 保存并打印
    save_result(conn, result)
    print_result(result)
    
    conn.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
