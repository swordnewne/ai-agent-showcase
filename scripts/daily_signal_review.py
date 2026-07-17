#!/usr/bin/env python3
"""
信号质量每日复盘系统 v2.0

核心变化：不再做"信号 vs 交易"的强行匹配（两者是独立系统），
改为评估信号本身的质量：信号触发后 N 日的实际涨跌是否符合信号方向。

用法: python3 daily_signal_review.py [--date YYYY-MM-DD]
"""
import sqlite3
import json
import os
import sys
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Tuple

def _find_workspace() -> str:
    """Find project root by looking for marker files"""
    path = os.path.dirname(os.path.abspath(__file__))
    while path != '/':
        if os.path.exists(os.path.join(path, 'AGENTS.md')) or os.path.exists(os.path.join(path, 'SOUL.md')):
            return path
        path = os.path.dirname(path)
    # Fallback: script-relative (3 levels up for scripts/ path)
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DB_PATH = os.path.join(_find_workspace(), "data", "finance.db")

# 2026 年中国法定节假日（需每年更新）
HOLIDAYS = {
    "2026-01-01",  # 元旦
    "2026-02-17", "2026-02-18", "2026-02-19", "2026-02-20", "2026-02-21", "2026-02-22", "2026-02-23",  # 春节
    "2026-04-04", "2026-04-05", "2026-04-06",  # 清明
    "2026-05-01", "2026-05-02", "2026-05-03",  # 劳动节
    "2026-06-19", "2026-06-20", "2026-06-21",  # 端午节
    "2026-09-25", "2026-09-26", "2026-09-27",  # 中秋节
    "2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04", "2026-10-05", "2026-10-06", "2026-10-07",  # 国庆
}

# akshare 缓存
_price_cache: Dict[str, float] = {}


def _normalize_code(code: str) -> str:
    """统一代码格式：纯代码 → 带后缀"""
    code = str(code).strip()
    if '.' in code:
        return code
    if code.startswith('6') or code.startswith('51') or code.startswith('68'):
        return f'{code}.SH'
    return f'{code}.SZ'


_spot_df = None  # 全局 spot 缓存，懒加载

def _load_spot_cache():
    """一次性加载全市场实时行情，用于获取当日收盘数据"""
    global _spot_df
    if _spot_df is None:
        try:
            import akshare as ak
            _spot_df = ak.stock_zh_a_spot()
        except Exception:
            _spot_df = None
    return _spot_df


def get_stock_close_price(symbol: str, date: str) -> Optional[float]:
    """获取某只票某日的收盘价（优先 akshare，失败返回 None）"""
    cache_key = f"{symbol}:{date}"
    if cache_key in _price_cache:
        return _price_cache[cache_key]
    
    try:
        import akshare as ak
        code = symbol.split('.')[0]
        df = ak.stock_zh_a_hist(symbol=code, period="daily", start_date=date.replace('-', ''), end_date=date.replace('-', ''), adjust="qfq")
        if not df.empty:
            price = float(df.iloc[0]['收盘'])
            _price_cache[cache_key] = price
            return price
    except Exception:
        pass
    
    # fallback: 新浪接口 stock_zh_a_daily
    try:
        import akshare as ak
        code = symbol.split('.')[0]
        exchange = 'sh' if symbol.endswith('.SH') else 'sz'
        sina_symbol = f"{exchange}{code}"
        df = ak.stock_zh_a_daily(symbol=sina_symbol, start_date=date.replace('-', ''), end_date=date.replace('-', ''))
        if not df.empty:
            price = float(df.iloc[0]['close'])
            _price_cache[cache_key] = price
            return price
    except Exception:
        pass
    
    # fallback: 实时行情 spot（用于获取当日收盘数据，收盘后最新价≈收盘价）
    try:
        today = datetime.now().strftime('%Y-%m-%d')
        if date == today:
            spot_df = _load_spot_cache()
            if spot_df is not None:
                code = symbol.split('.')[0]
                exchange = 'sh' if symbol.endswith('.SH') else 'sz'
                spot_code = f"{exchange}{code}"
                row = spot_df[spot_df['代码'] == spot_code]
                if not row.empty:
                    price = float(row.iloc[0]['最新价'])
                    _price_cache[cache_key] = price
                    return price
    except Exception:
        pass
    
    # 尝试从 finance.db 的 market_context 读取
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('SELECT close_price FROM market_context WHERE date = ? AND index_code = ?', (date, symbol))
        row = c.fetchone()
        conn.close()
        if row and row[0]:
            _price_cache[cache_key] = row[0]
            return row[0]
    except Exception:
        pass
    
    return None


def get_next_trading_date(date_str: str, days: int) -> Optional[str]:
    """获取 N 个交易日后的日期（跳过周末和法定节假日）"""
    d = datetime.strptime(date_str, '%Y-%m-%d')
    for _ in range(days):
        d += timedelta(days=1)
        while d.weekday() >= 5 or d.strftime('%Y-%m-%d') in HOLIDAYS:
            d += timedelta(days=1)
    return d.strftime('%Y-%m-%d')


def get_signals(conn, date: str) -> List[Tuple]:
    """读取某天所有信号（去重：同一股票同一天同一方向只取最新）"""
    c = conn.cursor()
    c.execute('''
        SELECT stock_code, signal_type, target_price, confidence, reason, created_at
        FROM sig_decision_signals
        WHERE DATE(created_at) = ?
        ORDER BY stock_code, created_at DESC
    ''', (date,))
    
    seen = set()
    signals = []
    for row in c.fetchall():
        code, side, price, conf, reason, created = row
        key = (code, side)
        if key in seen:
            continue
        seen.add(key)
        signals.append((code, side, price or 0, conf or 0, reason or '', created))
    return signals


def evaluate_signal_quality(date: str) -> Dict:
    """评估某天所有信号的质量"""
    conn = sqlite3.connect(DB_PATH)
    signals = get_signals(conn, date)
    conn.close()
    
    if not signals:
        return {'total': 0, 'message': '当天无信号'}
    
    results = {
        'date': date,
        'total_signals': len(signals),
        'buy_signals': [],
        'sell_signals': [],
        'hold_signals': [],
        'missing_price_count': 0,
        'missing_price_codes': [],
    }
    
    for code, side, price, conf, reason, created in signals:
        symbol = _normalize_code(code)
        
        # 标记价格缺失（入库时提取失败，非 akshare 问题）
        if price <= 0:
            results['missing_price_count'] += 1
            results['missing_price_codes'].append(symbol)
        
        # 获取后续价格
        next_1d = get_next_trading_date(date, 1)
        next_3d = get_next_trading_date(date, 3)
        next_5d = get_next_trading_date(date, 5)
        
        p_1d = get_stock_close_price(symbol, next_1d)
        p_3d = get_stock_close_price(symbol, next_3d)
        p_5d = get_stock_close_price(symbol, next_5d)
        
        result = {
            'symbol': symbol,
            'side': side,
            'signal_price': price,
            'confidence': conf,
            'reason': reason[:60],
        }
        
        if price > 0:
            if p_1d:
                result['return_1d'] = round((p_1d - price) / price * 100, 2)
            if p_3d:
                result['return_3d'] = round((p_3d - price) / price * 100, 2)
            if p_5d:
                result['return_5d'] = round((p_5d - price) / price * 100, 2)
        
        if side == 'buy':
            results['buy_signals'].append(result)
        elif side == 'sell':
            results['sell_signals'].append(result)
        else:
            results['hold_signals'].append(result)
    
    # 统计
    for side_key in ['buy_signals', 'sell_signals']:
        sigs = results[side_key]
        if not sigs:
            continue
        
        # 1日胜率
        returns_1d = [s.get('return_1d') for s in sigs if 'return_1d' in s]
        if returns_1d:
            if side_key == 'buy_signals':
                wins = sum(1 for r in returns_1d if r > 0)
            else:  # sell: 下跌为赢
                wins = sum(1 for r in returns_1d if r < 0)
            results[f'{side_key}_win_rate_1d'] = round(wins / len(returns_1d) * 100, 1)
            results[f'{side_key}_avg_return_1d'] = round(sum(returns_1d) / len(returns_1d), 2)
        
        # 5日胜率
        returns_5d = [s.get('return_5d') for s in sigs if 'return_5d' in s]
        if returns_5d:
            if side_key == 'buy_signals':
                wins = sum(1 for r in returns_5d if r > 0)
            else:
                wins = sum(1 for r in returns_5d if r < 0)
            results[f'{side_key}_win_rate_5d'] = round(wins / len(returns_5d) * 100, 1)
            results[f'{side_key}_avg_return_5d'] = round(sum(returns_5d) / len(returns_5d), 2)
    
    return results


def print_report(results: Dict):
    """打印复盘报告"""
    date = results.get('date', '?')
    total = results.get('total_signals', 0)
    missing = results.get('missing_price_count', 0)
    missing_codes = results.get('missing_price_codes', [])
    
    print(f"\n{'='*60}")
    print(f"📊 信号质量复盘 | {date}")
    print(f"{'='*60}")
    print(f"总信号: {total} 条")
    
    # 数据质量说明
    if missing > 0:
        print(f"⚠️  数据质量: {missing}/{total} 条信号触发价=0（入库时价格提取失败，非akshare问题）")
        if missing_codes:
            codes_str = ', '.join(missing_codes[:5])
            if len(missing_codes) > 5:
                codes_str += f' 等{len(missing_codes)}只'
            print(f"    涉及标的: {codes_str}")
    
    for side_key, label in [('buy_signals', '买入'), ('sell_signals', '卖出')]:
        sigs = results.get(side_key, [])
        if not sigs:
            continue
        
        print(f"\n{label}信号 ({len(sigs)}条):")
        win_1d = results.get(f'{side_key}_win_rate_1d', 0)
        avg_1d = results.get(f'{side_key}_avg_return_1d', 0)
        win_5d = results.get(f'{side_key}_win_rate_5d', 0)
        avg_5d = results.get(f'{side_key}_avg_return_5d', 0)
        
        valid_1d = sum(1 for s in sigs if 'return_1d' in s)
        print(f"  有效统计: {valid_1d}/{len(sigs)} 条（其余 price=0 无法计算）")
        if valid_1d > 0:
            print(f"  1日胜率: {win_1d}% | 1日平均: {avg_1d:+.2f}%")
            print(f"  5日胜率: {win_5d}% | 5日平均: {avg_5d:+.2f}%")
        
        # 明细
        for s in sigs[:5]:
            r1 = s.get('return_1d', '-')
            r5 = s.get('return_5d', '-')
            r1_str = f"{r1:+.2f}%" if isinstance(r1, (int, float)) else r1
            r5_str = f"{r5:+.2f}%" if isinstance(r5, (int, float)) else r5
            price_str = f"{s['signal_price']:.2f}" if s['signal_price'] > 0 else "缺失"
            print(f"    {s['symbol']:12} 信号价={price_str} 1日={r1_str} 5日={r5_str}")
        if len(sigs) > 5:
            print(f"    ... 共 {len(sigs)} 条")
    
    print(f"\n{'='*60}")
    print(f"💡 建议: 若 price=0 比例过高，检查 signal_pipeline._extract_price_from_content 正则匹配")
    print(f"{'='*60}\n")


def save_review(conn, date: str, results: Dict):
    """保存复盘结果到数据库（保留 signal_review 表结构，但改为质量评估）"""
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS sig_signal_review_v2 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            symbol TEXT NOT NULL,
            signal_side TEXT,
            signal_price REAL,
            return_1d REAL,
            return_3d REAL,
            return_5d REAL,
            is_correct INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(date, symbol, signal_side)
        )
    ''')
    
    for side_key in ['buy_signals', 'sell_signals']:
        for s in results.get(side_key, []):
            symbol = s['symbol']
            side = s['side']
            price = s['signal_price']
            r1 = s.get('return_1d')
            r3 = s.get('return_3d')
            r5 = s.get('return_5d')
            
            # 是否正确（buy 涨为对，sell 跌为对）
            is_correct = None
            if r1 is not None:
                if side == 'buy':
                    is_correct = 1 if r1 > 0 else 0
                elif side == 'sell':
                    is_correct = 1 if r1 < 0 else 0
            
            c.execute('''
                INSERT OR REPLACE INTO sig_signal_review_v2
                (date, symbol, signal_side, signal_price, return_1d, return_3d, return_5d, is_correct)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (date, symbol, side, price, r1, r3, r5, is_correct))
    
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
    results = evaluate_signal_quality(date)
    print_report(results)
    
    conn = sqlite3.connect(DB_PATH)
    save_review(conn, date, results)
    conn.close()
    print(f"✅ 已保存到 signal_review_v2")


if __name__ == '__main__':
    main()
