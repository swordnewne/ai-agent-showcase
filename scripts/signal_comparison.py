#!/usr/bin/env python3
"""信号组 vs 硬编码组 收益对比

基于 StratCraft Metrics 设计 + Backtrader 回归套件验证思路
轻量实现，不引入重型框架

用法:
    python3 signal_comparison.py
    python3 signal_comparison.py --days 30
"""

import sqlite3
import os
import json
import numpy as np
from dataclasses import dataclass, asdict
from typing import List, Optional, Dict, Tuple
from datetime import datetime, timedelta
import argparse

FINANCE_DB = os.path.join(
    os.path.dirname(__file__), "..", "..", "data", "finance.db"
)


@dataclass
class StrategyMetrics:
    """策略核心指标"""
    total_return_pct: float      # 累计收益率 %
    max_drawdown_pct: float      # 最大回撤 %
    sharpe_ratio: float          # 夏普比率
    win_rate_pct: float          # 胜率 %
    avg_profit_pct: float        # 平均盈利 %
    avg_loss_pct: float          # 平均亏损 %
    profit_factor: float         # 盈亏比
    trade_count: int             # 交易次数
    holding_period_days: float # 平均持仓天数


def compute_metrics(returns: List[float], 
                    trades: Optional[List[Dict]] = None) -> StrategyMetrics:
    """从日收益率列表计算指标
    
    Args:
        returns: 每日收益率列表 (0.01 = 1%)
        trades: 可选，交易记录列表（含 profit/loss, holding_days）
    """
    returns = np.array(returns)
    
    # 累计收益率
    cumulative = (1 + returns).cumprod()
    total_return = (cumulative[-1] - 1) * 100 if len(cumulative) > 0 else 0.0
    
    # 最大回撤
    rolling_max = np.maximum.accumulate(cumulative)
    drawdowns = (cumulative - rolling_max) / rolling_max
    max_drawdown = abs(drawdowns.min()) * 100 if len(drawdowns) > 0 else 0.0
    
    # 夏普（假设无风险利率=0，简单处理）
    if len(returns) > 1 and returns.std() > 0:
        sharpe = (returns.mean() * 252) / (returns.std() * np.sqrt(252))
    else:
        sharpe = 0.0
    
    # 从交易记录计算胜率/盈亏比
    if trades:
        profits = [t['return'] for t in trades if t['return'] > 0]
        losses = [t['return'] for t in trades if t['return'] < 0]
        win_rate = (len(profits) / len(trades)) * 100 if trades else 0
        avg_profit = np.mean(profits) * 100 if profits else 0
        avg_loss = abs(np.mean(losses)) * 100 if losses else 0
        profit_factor = (sum(profits) / abs(sum(losses))) if losses and sum(losses) < 0 else float('inf')
        holding_days = np.mean([t.get('holding_days', 1) for t in trades])
        trade_count = len(trades)
    else:
        # 没有交易记录，从日收益推导
        positive = returns[returns > 0]
        negative = returns[returns < 0]
        win_rate = (len(positive) / len(returns)) * 100 if len(returns) > 0 else 0
        avg_profit = np.mean(positive) * 100 if len(positive) > 0 else 0
        avg_loss = abs(np.mean(negative)) * 100 if len(negative) > 0 else 0
        profit_factor = (sum(positive) / abs(sum(negative))) if len(negative) > 0 else 1.0
        holding_days = 1.0
        trade_count = len(returns)
    
    return StrategyMetrics(
        total_return_pct=round(total_return, 2),
        max_drawdown_pct=round(max_drawdown, 2),
        sharpe_ratio=round(sharpe, 2),
        win_rate_pct=round(win_rate, 2),
        avg_profit_pct=round(avg_profit, 2),
        avg_loss_pct=round(avg_loss, 2),
        profit_factor=round(profit_factor, 2),
        trade_count=trade_count,
        holding_period_days=round(holding_days, 1)
    )


def load_signal_trades(days: int = 90) -> List[Dict]:
    """从 sig_decision_signals 加载信号组交易记录"""
    conn = sqlite3.connect(FINANCE_DB)
    cursor = conn.cursor()
    
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    
    # 先检查 verified signals（有 outcome_return）
    cursor.execute("""
        SELECT signal_id, stock_code, signal_type, target_price, 
               stop_loss, created_at, outcome_return, verified_days
        FROM sig_decision_signals
        WHERE created_at >= ? AND outcome_return IS NOT NULL
        ORDER BY created_at
    """, (cutoff,))
    
    trades = []
    for row in cursor.fetchall():
        trades.append({
            'id': row[0],
            'symbol': row[1],
            'type': row[2],
            'target': row[3],
            'stop': row[4],
            'date': row[5],
            'return': row[6] or 0.0,
            'holding_days': row[7] or 1
        })
    
    # 如果没有 verified，退回到 signal_review_v2（更频繁的短期回测）
    if not trades:
        cutoff_short = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
        cursor.execute("""
            SELECT symbol, signal_side, signal_price, return_1d, is_correct, date
            FROM sig_signal_review_v2
            WHERE date >= ? AND return_1d IS NOT NULL
            ORDER BY date
        """, (cutoff_short,))
        for row in cursor.fetchall():
            trades.append({
                'id': f"{row[0]}_{row[5]}",
                'symbol': row[0],
                'type': row[1],
                'target': row[2],
                'date': row[5],
                'return': row[3] / 100 if row[3] else 0.0,  # 1d return 是百分比值
                'holding_days': 1,
                'is_correct': row[4]
            })
    
    conn.close()
    return trades


def load_hardcoded_trades(days: int = 90) -> List[Dict]:
    """从 jq_portfolio_events 加载硬编码组（手动交易）记录"""
    conn = sqlite3.connect(FINANCE_DB)
    cursor = conn.cursor()
    
    cutoff = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
    cutoff = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
    
    # 优先：用 portfolio_value 日收益
    cursor.execute("""
        SELECT total_pnl_pct FROM sig_portfolio_value
        WHERE date >= ? ORDER BY date
    """, (cutoff,))
    portfolio_returns = [r[0] / 100 for r in cursor.fetchall() if r[0] is not None]
    
    if portfolio_returns:
        conn.close()
        return [{'return': r, 'holding_days': 1} for r in portfolio_returns]
    
    # 备用：jq_portfolio_events 交易记录
    cursor.execute("""
        SELECT id, symbol, side, price, quantity, event_date
        FROM jq_portfolio_events
        WHERE event_date >= ? AND event_type = 'order'
        ORDER BY event_date
    """, (cutoff,))
    
    rows = cursor.fetchall()
    if not rows:
        conn.close()
        return []
    
    # 简化处理：用买入/卖出配对计算收益
    # 实际应更精确，但此处用近似
    trades = []
    for row in rows:
        trades.append({
            'id': row[0],
            'symbol': row[1],
            'side': row[2],
            'price': row[3],
            'quantity': row[4],
            'date': row[5],
            'return': 0.0,  # 需配对计算，此处占位
            'holding_days': 1
        })
    
    # 简化：硬编码组如无完整收益，用 portfolio_value 表日收益
    cursor.execute("""
        SELECT total_pnl_pct FROM sig_portfolio_value
        WHERE date >= ? ORDER BY date
    """, (cutoff,))
    portfolio_returns = [r[0] / 100 for r in cursor.fetchall() if r[0] is not None]
    
    conn.close()
    
    if portfolio_returns:
        return [{'return': r, 'holding_days': 1} for r in portfolio_returns]
    return trades


def load_market_benchmark(days: int = 90) -> List[float]:
    """从 sig_market_context 加载大盘日收益（作为基准）"""
    conn = sqlite3.connect(FINANCE_DB)
    cursor = conn.cursor()
    
    cutoff = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
    cursor.execute("""
        SELECT sh_index_change FROM sig_market_context
        WHERE trade_date >= ? AND sh_index_change IS NOT NULL
        ORDER BY trade_date
    """, (cutoff,))
    
    returns = [r[0] / 100 for r in cursor.fetchall()]
    conn.close()
    return returns


def compare_strategies(days: int = 90) -> Dict:
    """对比三组策略：信号组 vs 硬编码组 vs 大盘基准"""
    print(f"📊 分析最近 {days} 天...")
    
    # 信号组
    signal_trades = load_signal_trades(days)
    if signal_trades:
        signal_returns = [t['return'] for t in signal_trades]
        signal_metrics = compute_metrics(signal_returns, signal_trades)
    else:
        signal_metrics = None
    
    # 硬编码组
    hardcoded_trades = load_hardcoded_trades(days)
    if hardcoded_trades and all('return' in t for t in hardcoded_trades):
        hardcoded_returns = [t['return'] for t in hardcoded_trades]
        hardcoded_metrics = compute_metrics(hardcoded_returns, hardcoded_trades)
    else:
        hardcoded_metrics = None
    
    # 大盘基准
    benchmark_returns = load_market_benchmark(days)
    if benchmark_returns:
        benchmark_metrics = compute_metrics(benchmark_returns)
    else:
        benchmark_metrics = None
    
    return {
        'signal_group': asdict(signal_metrics) if signal_metrics else None,
        'hardcoded_group': asdict(hardcoded_metrics) if hardcoded_metrics else None,
        'benchmark': asdict(benchmark_metrics) if benchmark_metrics else None,
        'sample': {
            'signal_trades': len(signal_trades),
            'hardcoded_trades': len(hardcoded_trades),
            'benchmark_days': len(benchmark_returns)
        }
    }


def format_report(result: Dict) -> str:
    """格式化对比报告"""
    lines = []
    lines.append("📈 信号组 vs 硬编码组 收益对比报告")
    lines.append("=" * 50)
    lines.append(f"样本：信号 {result['sample']['signal_trades']} 笔 | 硬编码 {result['sample']['hardcoded_trades']} 笔 | 大盘 {result['sample']['benchmark_days']} 天")
    lines.append("")
    
    groups = [
        ('🤖 信号组', result['signal_group']),
        ('🔧 硬编码组', result['hardcoded_group']),
        ('📊 大盘基准', result['benchmark'])
    ]
    
    for name, metrics in groups:
        if not metrics:
            lines.append(f"{name}: 数据不足")
            continue
        lines.append(f"{name}")
        lines.append(f"  累计收益率: {metrics['total_return_pct']:+.2f}%")
        lines.append(f"  最大回撤: {metrics['max_drawdown_pct']:.2f}%")
        lines.append(f"  夏普比率: {metrics['sharpe_ratio']:.2f}")
        lines.append(f"  胜率: {metrics['win_rate_pct']:.1f}%")
        lines.append(f"  盈亏比: {metrics['profit_factor']:.2f}")
        lines.append(f"  交易次数: {metrics['trade_count']}")
        lines.append("")
    
    # 对比分析
    sig = result['signal_group']
    hard = result['hardcoded_group']
    bench = result['benchmark']
    
    if sig and bench:
        alpha = sig['total_return_pct'] - bench['total_return_pct']
        lines.append(f"💡 信号组超额收益 (α): {alpha:+.2f}%")
    
    if sig and hard:
        diff = sig['total_return_pct'] - hard['total_return_pct']
        if diff > 0:
            lines.append(f"✅ 信号组领先硬编码: {diff:+.2f}%")
        else:
            lines.append(f"⚠️ 硬编码领先信号组: {abs(diff):.2f}%")
    
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description='策略收益对比')
    parser.add_argument('--days', type=int, default=90, help='分析天数')
    parser.add_argument('--output', type=str, help='输出JSON文件路径')
    args = parser.parse_args()
    
    result = compare_strategies(args.days)
    
    print(format_report(result))
    
    if args.output:
        with open(args.output, 'w') as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        print(f"\n📁 结果已保存: {args.output}")
    
    return result


if __name__ == '__main__':
    main()
