#!/usr/bin/env python3
"""
信号 vs 实盘 每日复盘系统（含虚拟收益计算）
用法: python3 daily_signal_review.py [--date YYYY-MM-DD]

流程:
1. 读取当天信号 (decision_signals)
2. 读取当天实盘 (portfolio_events trades + positions)
3. 对比: 命中率、价格偏差、收益差异
4. 虚拟收益: 信号建议价 → 次日收盘价 → 收益率
5. 生成报告
"""
import sqlite3
import json
import os
import sys
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import List, Optional, Dict

DB_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', 'finance.db')

NAME_MAP = {
    '002008.SZ': '大族激光', '002475.SZ': '立讯精密', '159915.SZ': '创业板ETF',
    '300223.SZ': '北京君正', '300308.SZ': '中际旭创', '300433.SZ': '蓝思科技',
    '300502.SZ': '新易盛', '300661.SZ': '圣邦股份', '510300.SH': '300ETF',
    '510500.SH': '500ETF', '688256.SH': '寒武纪', '688390.SH': '固德威',
}

# akshare 缓存，避免重复查询
_price_cache: Dict[str, Dict[str, float]] = {}


@dataclass
class Signal:
    date: str
    symbol: str
    side: str
    signal_price: float
    confidence: int
    reason: str
    score_total: int
    kelly_fraction: float


@dataclass
class Trade:
    date: str
    symbol: str
    side: str
    quantity: float
    price: float


@dataclass
class ComparisonResult:
    symbol: str
    name: str
    signal_side: str
    signal_price: float
    has_trade: bool
    trade_side: Optional[str]
    trade_price: Optional[float]
    trade_qty: Optional[float]
    price_diff: Optional[float]
    match: bool
    pnl_signal_1d: Optional[float] = None   # 信号次日收益
    pnl_signal_5d: Optional[float] = None   # 信号5日收益
    pnl_actual_1d: Optional[float] = None   # 实盘次日收益
    next_price: Optional[float] = None      # 次日收盘价


def _strip_suffix(symbol: str) -> str:
    """688256.SH → 688256"""
    return symbol.split('.')[0]


def _is_etf(symbol: str) -> bool:
    """判断是否是ETF"""
    return symbol.startswith('15') or symbol.startswith('51')


def get_stock_close_price(symbol: str, date: str) -> Optional[float]:
    """获取某只票某日的收盘价，优先缓存"""
    cache_key = f"{symbol}:{date}"
    if cache_key in _price_cache:
        return _price_cache[cache_key]
    
    try:
        import akshare as ak
        code = _strip_suffix(symbol)
        
        if _is_etf(symbol):
            df = ak.fund_etf_hist_em(symbol=code, period="daily",
                                     start_date=date.replace('-', ''),
                                     end_date=date.replace('-', ''),
                                     adjust="qfq")
        else:
            df = ak.stock_zh_a_hist(symbol=code, period="daily",
                                    start_date=date.replace('-', ''),
                                    end_date=date.replace('-', ''),
                                    adjust="qfq")
        if df is not None and len(df) > 0:
            price = float(df.iloc[0]['收盘'])
            _price_cache[cache_key] = price
            return price
    except Exception as e:
        pass
    
    return None


def get_next_trading_date(date_str: str, days: int = 1) -> str:
    """获取N个交易日后的日期（跳过周末）"""
    d = datetime.strptime(date_str, '%Y-%m-%d')
    count = 0
    while count < days:
        d += timedelta(days=1)
        if d.weekday() < 5:
            count += 1
    return d.strftime('%Y-%m-%d')


def calculate_virtual_pnl(signal: Signal, hold_days: int = 1) -> Optional[float]:
    """计算信号的虚拟收益
    buy:  (next_close - signal_price) / signal_price * 100
    sell: (signal_price - next_close) / signal_price * 100
    """
    if signal.signal_price <= 0:
        return None
    
    next_date = get_next_trading_date(signal.date, hold_days)
    next_close = get_stock_close_price(signal.symbol, next_date)
    if next_close is None:
        return None
    
    if signal.side == 'buy':
        return (next_close - signal.signal_price) / signal.signal_price * 100
    elif signal.side == 'sell':
        return (signal.signal_price - next_close) / signal.signal_price * 100
    else:
        return None


def normalize_symbol(code: str) -> str:
    code = str(code).strip()
    if '.' in code:
        return code
    if code.startswith('6') or code.startswith('51') or code.startswith('68'):
        return f'{code}.SH'
    return f'{code}.SZ'


def get_signals(conn, date: str) -> List[Signal]:
    cursor = conn.cursor()
    cursor.execute('''
        SELECT created_at, stock_code, signal_type, target_price, 
               confidence, reason, score_total, kelly_fraction
        FROM decision_signals
        WHERE DATE(created_at) = ?
        ORDER BY stock_code
    ''', (date,))
    
    signals = []
    for row in cursor.fetchall():
        created_at, code, side, price, conf, reason, score, kelly = row
        symbol = normalize_symbol(code)
        signals.append(Signal(
            date=date, symbol=symbol, side=side or 'hold',
            signal_price=price or 0, confidence=conf or 0,
            reason=reason or '', score_total=score or 0,
            kelly_fraction=kelly or 0
        ))
    return signals


def get_trades(conn, date: str) -> List[Trade]:
    cursor = conn.cursor()
    cursor.execute('''
        SELECT event_date, symbol, side, quantity, price
        FROM portfolio_events
        WHERE event_type = 'trade' AND event_date = ?
        ORDER BY symbol
    ''', (date,))
    
    trades = []
    for row in cursor.fetchall():
        trades.append(Trade(
            date=row[0], symbol=row[1], side=row[2] or 'hold',
            quantity=row[3] or 0, price=row[4] or 0
        ))
    return trades


def get_positions(conn, date: str) -> dict:
    cursor = conn.cursor()
    cursor.execute('''
        SELECT symbol, quantity, price
        FROM portfolio_events
        WHERE event_type = 'position' AND event_date = ?
    ''', (date,))
    return {row[0]: (row[1], row[2]) for row in cursor.fetchall()}


def compare_signals_trades(signals: List[Signal], trades: List[Trade],
                           positions: dict, prev_positions: dict) -> List[ComparisonResult]:
    trade_map = {}
    for t in trades:
        trade_map.setdefault(t.symbol, []).append(t)
    
    results = []
    
    # 1. 遍历信号
    for sig in signals:
        trades_for_symbol = trade_map.get(sig.symbol, [])
        matching_trade = next((t for t in trades_for_symbol if t.side == sig.side), None)
        
        prev_qty = prev_positions.get(sig.symbol, (0, 0))[0]
        curr_qty = positions.get(sig.symbol, (0, 0))[0]
        qty_changed = curr_qty != prev_qty
        
        actual_price = matching_trade.price if matching_trade else (
            positions.get(sig.symbol, (0, 0))[1] if qty_changed else None
        )
        
        price_diff = None
        if actual_price and sig.signal_price:
            price_diff = sig.signal_price - actual_price
        
        match = False
        if sig.side == 'buy' and curr_qty > prev_qty:
            match = True
        elif sig.side == 'sell' and curr_qty < prev_qty:
            match = True
        elif sig.side == 'hold' and not qty_changed:
            match = True
        
        # 计算虚拟收益
        pnl_1d = calculate_virtual_pnl(sig, hold_days=1)
        pnl_5d = calculate_virtual_pnl(sig, hold_days=5)
        
        # 实盘次日收益（如果有实际成交）
        pnl_actual = None
        if actual_price and match:
            next_date = get_next_trading_date(sig.date, 1)
            next_close = get_stock_close_price(sig.symbol, next_date)
            if next_close:
                pnl_actual = (next_close - actual_price) / actual_price * 100
        
        results.append(ComparisonResult(
            symbol=sig.symbol, name=NAME_MAP.get(sig.symbol, sig.symbol),
            signal_side=sig.side, signal_price=sig.signal_price,
            has_trade=matching_trade is not None or qty_changed,
            trade_side=matching_trade.side if matching_trade else (sig.side if qty_changed else None),
            trade_price=actual_price,
            trade_qty=abs(curr_qty - prev_qty) if qty_changed else None,
            price_diff=price_diff, match=match,
            pnl_signal_1d=pnl_1d, pnl_signal_5d=pnl_5d,
            pnl_actual_1d=pnl_actual
        ))
    
    # 2. 遗漏检测
    signaled_symbols = {s.symbol for s in signals}
    for symbol, trades_list in trade_map.items():
        if symbol not in signaled_symbols:
            for t in trades_list:
                results.append(ComparisonResult(
                    symbol=symbol, name=NAME_MAP.get(symbol, symbol),
                    signal_side='none', signal_price=0, has_trade=True,
                    trade_side=t.side, trade_price=t.price, trade_qty=t.quantity,
                    price_diff=None, match=False
                ))
    
    return results


def print_report(date: str, signals: List[Signal], results: List[ComparisonResult]):
    print(f'\n=== {date} 信号 vs 实盘复盘 ===\n')
    
    total_signals = len(signals)
    matched = sum(1 for r in results if r.match and r.signal_side != 'none')
    missed = sum(1 for r in results if r.signal_side == 'none')
    
    print(f'【概览】')
    print(f'  今日信号: {total_signals} 条')
    print(f'  命中: {matched} / {total_signals} ({matched/total_signals*100:.0f}%)' if total_signals else '  命中: N/A')
    print(f'  遗漏: {missed} 只')
    print(f'')
    
    # 详细对比表
    print(f'【详细对比】')
    print(f'{"代码":<15} {"名称":<10} {"信号":<6} {"信号价":>10} {"实盘":<6} {"实盘价":>10} {"偏差":>10} {"1日收益":>10} {"5日收益":>10} {"结果":<6}')
    print('-' * 115)
    
    for r in results:
        if r.signal_side == 'none':
            print(f"{r.symbol:<15} {r.name:<10} {'--':<6} {'--':>10} {r.trade_side:<6} {r.trade_price:>10.2f} {'--':>10} {'--':>10} {'--':>10} {'❌遗漏':<6}")
        else:
            sig_price = f"{r.signal_price:.2f}" if r.signal_price else '--'
            trade_price = f"{r.trade_price:.2f}" if r.trade_price else '--'
            diff = f"{r.price_diff:+.2f}" if r.price_diff is not None else '--'
            pnl1 = f"{r.pnl_signal_1d:+.2f}%" if r.pnl_signal_1d is not None else '--'
            pnl5 = f"{r.pnl_signal_5d:+.2f}%" if r.pnl_signal_5d is not None else '--'
            result = '✅命中' if r.match else '❌偏离'
            
            print(f"{r.symbol:<15} {r.name:<10} {r.signal_side:<6} {sig_price:>10} {r.trade_side or '--':<6} {trade_price:>10} {diff:>10} {pnl1:>10} {pnl5:>10} {result:<6}")
    
    print('')
    
    # 价格偏差分析
    price_diffs = [r.price_diff for r in results if r.price_diff is not None]
    if price_diffs:
        avg_diff = sum(price_diffs) / len(price_diffs)
        better_count = sum(1 for d in price_diffs if d > 0)
        print(f'【价格分析】')
        print(f'  平均偏差: {avg_diff:+.2f}（正=信号更优）')
        print(f'  信号更优: {better_count} / {len(price_diffs)}')
        print(f'')
    
    # 虚拟收益分析
    buy_signals = [r for r in results if r.signal_side == 'buy' and r.pnl_signal_1d is not None]
    if buy_signals:
        avg_pnl_1d = sum(r.pnl_signal_1d for r in buy_signals) / len(buy_signals)
        avg_pnl_5d = sum(r.pnl_signal_5d for r in buy_signals if r.pnl_signal_5d is not None) / max(1, sum(1 for r in buy_signals if r.pnl_signal_5d is not None))
        win_count = sum(1 for r in buy_signals if r.pnl_signal_1d > 0)
        
        print(f'【虚拟收益分析（买入信号）】')
        print(f'  样本: {len(buy_signals)} 只')
        print(f'  次日平均收益: {avg_pnl_1d:+.2f}%')
        print(f'  5日平均收益: {avg_pnl_5d:+.2f}%')
        print(f'  次日胜率: {win_count} / {len(buy_signals)} ({win_count/len(buy_signals)*100:.0f}%)')
        
        # 命中 vs 未命中的收益对比
        matched_buys = [r for r in buy_signals if r.match]
        missed_buys = [r for r in buy_signals if not r.match]
        if matched_buys:
            avg_matched = sum(r.pnl_signal_1d for r in matched_buys) / len(matched_buys)
            print(f'  命中信号次日收益: {avg_matched:+.2f}%')
        if missed_buys:
            avg_missed = sum(r.pnl_signal_1d for r in missed_buys) / len(missed_buys)
            print(f'  未命中信号次日收益: {avg_missed:+.2f}%')
        print(f'')
    
    # 实盘收益对比
    actual_buys = [r for r in results if r.match and r.pnl_actual_1d is not None]
    if actual_buys:
        avg_actual = sum(r.pnl_actual_1d for r in actual_buys) / len(actual_buys)
        avg_signal = sum(r.pnl_signal_1d for r in actual_buys if r.pnl_signal_1d is not None) / max(1, sum(1 for r in actual_buys if r.pnl_signal_1d is not None))
        print(f'【实盘 vs 信号收益对比】')
        print(f'  实盘次日平均收益: {avg_actual:+.2f}%')
        print(f'  信号次日平均收益: {avg_signal:+.2f}%')
        print(f'  差值: {avg_signal - avg_actual:+.2f}%（正=信号更优）')
        print(f'')


def save_review(conn, date: str, results: List[ComparisonResult]):
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS signal_review (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL, symbol TEXT NOT NULL,
            signal_side TEXT, signal_price REAL,
            trade_side TEXT, trade_price REAL,
            match INTEGER, price_diff REAL,
            pnl_signal_1d REAL, pnl_signal_5d REAL, pnl_actual_1d REAL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(date, symbol)
        )
    ''')
    
    for r in results:
        cursor.execute('''
            INSERT OR REPLACE INTO signal_review
            (date, symbol, signal_side, signal_price, trade_side, trade_price, match, price_diff, pnl_signal_1d, pnl_signal_5d, pnl_actual_1d)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (date, r.symbol, r.signal_side, r.signal_price, r.trade_side,
              r.trade_price, 1 if r.match else 0, r.price_diff,
              r.pnl_signal_1d, r.pnl_signal_5d, r.pnl_actual_1d))
    
    conn.commit()


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--date', type=str, help='复盘日期 (YYYY-MM-DD)，默认昨天')
    args = parser.parse_args()
    
    if args.date:
        date = args.date
    else:
        d = datetime.now() - timedelta(days=1)
        while d.weekday() >= 5:
            d -= timedelta(days=1)
        date = d.strftime('%Y-%m-%d')
    
    print(f'正在复盘 {date}...')
    
    conn = sqlite3.connect(DB_PATH)
    prev_date = (datetime.strptime(date, '%Y-%m-%d') - timedelta(days=1)).strftime('%Y-%m-%d')
    
    signals = get_signals(conn, date)
    trades = get_trades(conn, date)
    positions = get_positions(conn, date)
    prev_positions = get_positions(conn, prev_date)
    
    if not signals:
        print(f"警告: {date} 没有信号记录")
        print("提示: 信号需要先录入到 decision_signals 表")
        return 0
    
    results = compare_signals_trades(signals, trades, positions, prev_positions)
    print_report(date, signals, results)
    save_review(conn, date, results)
    
    conn.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
