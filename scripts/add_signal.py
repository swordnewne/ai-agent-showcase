#!/usr/bin/env python3
"""
信号录入工具
用法: 
  python3 add_signal.py --date 2026-06-26 --code 688256 --side buy --price 1480 --reason "芯片利好" --confidence 8
  python3 add_signal.py --batch signals.json
"""
import sqlite3
import json
import os
import sys
from datetime import datetime, timezone

DB_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', 'finance.db')


def ensure_table(conn):
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS decision_signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_id TEXT UNIQUE,
            stock_code TEXT NOT NULL,
            stock_name TEXT,
            signal_type TEXT NOT NULL,
            confidence INTEGER DEFAULT 0,
            reason TEXT,
            score_total INTEGER DEFAULT 0,
            score_breakdown TEXT,
            kelly_fraction REAL DEFAULT 0,
            target_price REAL,
            stop_loss REAL,
            suggested_shares INTEGER DEFAULT 0,
            market_context TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            outcome TEXT,
            outcome_return REAL,
            verified_at TIMESTAMP,
            verified_days INTEGER DEFAULT 5
        )
    ''')
    conn.commit()


def add_signal(date: str, code: str, side: str, price: float, 
               reason: str = '', confidence: int = 0, name: str = ''):
    """录入单条信号"""
    conn = sqlite3.connect(DB_PATH)
    ensure_table(conn)
    cursor = conn.cursor()
    
    # 生成 signal_id
    signal_id = f"SIG-{date.replace('-', '')}-{code}"
    
    # 如果没有给名称，尝试推断
    if not name:
        name_map = {
            '002008': '大族激光', '002475': '立讯精密', '159915': '创业板ETF',
            '300223': '北京君正', '300308': '中际旭创', '300433': '蓝思科技',
            '300502': '新易盛', '300661': '圣邦股份', '510300': '300ETF',
            '510500': '500ETF', '688256': '寒武纪', '688390': '固德威',
        }
        name = name_map.get(code, code)
    
    cursor.execute('''
        INSERT OR REPLACE INTO decision_signals
        (signal_id, stock_code, stock_name, signal_type, target_price, 
         confidence, reason, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (signal_id, code, name, side, price, confidence, reason, 
          f"{date} 09:30:00"))
    
    conn.commit()
    conn.close()
    print(f"✅ 信号已录入: {date} {code} {side} @ {price}")
    return signal_id


def batch_add(json_file: str):
    """批量录入信号"""
    with open(json_file, 'r', encoding='utf-8') as f:
        signals = json.load(f)
    
    for sig in signals:
        add_signal(
            date=sig['date'],
            code=sig['code'],
            side=sig['side'],
            price=sig['price'],
            reason=sig.get('reason', ''),
            confidence=sig.get('confidence', 0),
            name=sig.get('name', '')
        )
    
    print(f"\n批量录入完成: {len(signals)} 条信号")


def interactive_add():
    """交互式录入"""
    print("=== 信号录入 ===")
    print("格式: 日期 代码 方向 价格 [理由] [置信度]")
    print("示例: 2026-06-26 688256 buy 1480 芯片利好 8")
    print("输入 'done' 结束\n")
    
    while True:
        line = input("> ").strip()
        if line.lower() == 'done':
            break
        
        parts = line.split()
        if len(parts) < 4:
            print("格式错误，请重试")
            continue
        
        date, code, side, price = parts[0], parts[1], parts[2], float(parts[3])
        reason = parts[4] if len(parts) > 4 else ''
        confidence = int(parts[5]) if len(parts) > 5 else 0
        
        add_signal(date, code, side, price, reason, confidence)


def main():
    import argparse
    parser = argparse.ArgumentParser(description='信号录入工具')
    parser.add_argument('--date', type=str, help='信号日期 YYYY-MM-DD')
    parser.add_argument('--code', type=str, help='股票代码')
    parser.add_argument('--side', type=str, choices=['buy', 'sell', 'hold'], help='方向')
    parser.add_argument('--price', type=float, help='建议价格')
    parser.add_argument('--reason', type=str, default='', help='理由')
    parser.add_argument('--confidence', type=int, default=0, help='置信度 0-10')
    parser.add_argument('--name', type=str, default='', help='股票名称')
    parser.add_argument('--batch', type=str, help='批量录入 JSON 文件')
    parser.add_argument('--interactive', action='store_true', help='交互式录入')
    
    args = parser.parse_args()
    
    if args.batch:
        batch_add(args.batch)
    elif args.interactive:
        interactive_add()
    elif args.date and args.code and args.side and args.price is not None:
        add_signal(args.date, args.code, args.side, args.price, 
                   args.reason, args.confidence, args.name)
    else:
        parser.print_help()
        print("\n示例:")
        print("  python3 add_signal.py --date 2026-06-26 --code 688256 --side buy --price 1480 --reason \"芯片利好\" --confidence 8")
        return 1
    
    return 0


if __name__ == '__main__':
    sys.exit(main())
