#!/usr/bin/env python3
"""
聚宽模拟盘 每日市值自动更新（akshare版）
用法: python3 update_portfolio_value_akshare.py [--date YYYY-MM-DD] [--force]

自动流程:
1. 从数据库读取最新持仓
2. 用 akshare 获取实时价格
3. 计算市值和盈亏
4. 保存到 portfolio_value 表
"""
import sqlite3
import os
import sys
import json
import re
from datetime import datetime, timezone

# 尝试导入 akshare，如果失败给出提示
try:
    import akshare as ak
except ImportError:
    print("错误: 未安装 akshare")
    print("安装: pip install akshare")
    sys.exit(1)

DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "finance.db"
)

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


def get_positions(conn):
    """获取最新持仓"""
    cursor = conn.cursor()
    cursor.execute('''
        SELECT event_date, symbol, quantity, price 
        FROM sig_portfolio_events 
        WHERE event_type = 'position'
        ORDER BY event_date DESC, symbol
    ''')
    rows = cursor.fetchall()
    if not rows:
        return []
    latest_date = rows[0][0]
    return [r for r in rows if r[0] == latest_date]


def get_price_akshare(symbol, date_str):
    """用 akshare 获取指定日期收盘价"""
    # 从 symbol 提取代码和交易所
    # 002008.SZ -> 002008, SZ
    code = symbol.split('.')[0]
    
    try:
        # 判断是股票还是ETF
        if symbol.startswith('51') or symbol.startswith('15') or symbol.startswith('16'):
            # ETF
            df = ak.fund_etf_hist_em(symbol=code, period='daily', 
                                      start_date=date_str, end_date=date_str, 
                                      adjust='qfq')
        else:
            # 股票
            df = ak.stock_zh_a_hist(symbol=code, period='daily',
                                     start_date=date_str, end_date=date_str,
                                     adjust='qfq')
        
        if df.empty:
            return None
        
        return float(df.iloc[0]['收盘'])
    except Exception as e:
        print(f"  获取 {symbol} 价格失败: {e}")
        return None


def fetch_all_prices(symbols, date_str):
    """批量获取价格"""
    prices = {}
    print(f"获取 {date_str} 实时价格...")
    
    for symbol in symbols:
        price = get_price_akshare(symbol, date_str)
        if price:
            prices[symbol] = price
            print(f"  {symbol}: {price}")
        else:
            # 获取失败，返回 None，后续用成本价兜底
            print(f"  {symbol}: 获取失败")
    
    return prices


def calculate(positions, prices):
    """计算组合市值"""
    total_cost = 0
    total_value = 0
    holdings = []
    
    for date, symbol, qty, cost in positions:
        qty = float(qty) if qty else 0
        cost = float(cost) if cost else 0
        
        # 获取不到价格时，用成本价兜底
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
    print(f'')
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
    
    total_asset = 1008447
    cash = total_asset - result['total_value']
    print(f'  估算现金: {cash:,.0f}')
    print(f'  仓位比例: {result["total_value"] / total_asset * 100:.1f}%')


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--date', type=str, help='指定日期 (YYYY-MM-DD)，默认用持仓日期')
    parser.add_argument('--force', action='store_true', help='强制更新（即使当天已更新）')
    args = parser.parse_args()
    
    # 连接数据库
    conn = sqlite3.connect(DB_PATH)
    ensure_table(conn)
    
    # 获取持仓
    positions = get_positions(conn)
    if not positions:
        print("错误: 没有找到持仓数据")
        return 1
    
    # 确定日期
    position_date = positions[0][0]
    target_date = args.date or position_date
    date_str = target_date.replace('-', '')  # YYYY-MM-DD -> YYYYMMDD
    
    # 检查是否已更新（仅当不指定日期且不强制时）
    if not args.force and not args.date:
        cursor = conn.cursor()
        cursor.execute('SELECT id FROM portfolio_value WHERE date = ?', (target_date,))
        if cursor.fetchone():
            print(f"{target_date} 已更新，跳过（加 --force 强制更新）")
            return 0
    
    # 获取价格
    symbols = [p[1] for p in positions]
    prices = fetch_all_prices(symbols, date_str)
    
    if not prices:
        print("错误: 未能获取任何价格")
        return 1
    
    # 计算
    result = calculate(positions, prices)
    result['date'] = target_date
    
    # 保存并打印
    save_result(conn, result)
    print_result(result)
    
    conn.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
