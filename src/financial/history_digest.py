#!/usr/bin/env python3
"""P1.2 候选历史行情摘要：从本地 BaoStock PG 库生成有限上下文摘要。

口径（账本 P1.2）：普通候选最近 250 个完整交易日，重点候选 750 日。
摘要必须含：数据截止日、复权口径、实际条数、缺失日、重复日、证券状态。
十年完整日线留在数据库，本模块只产出摘要 + 紧凑序列，不替代库。

用法：
  python3 history_digest.py --asof 2026-10-12 --codes 600312.SH,000598.SZ [--focus 600312.SH]
"""
import argparse
import hashlib
import json
import statistics
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import psycopg  # noqa: E402
import rising_streak  # noqa: E402  复用 DATASET/SOURCE/DSN 与 finance-adapter 路径注入
from trade_calendar import is_trading_day  # noqa: E402

CST = timezone(timedelta(hours=8))
WINDOW_NORMAL = 250
WINDOW_FOCUS = 750


def build_digest(conn, scope, security_id: str, asof: str, focus: bool = False) -> dict:
    window = WINDOW_FOCUS if focus else WINDOW_NORMAL
    with conn.cursor() as cur:
        cur.execute(
            """SELECT trade_date, close, preclose, pct_chg, volume, turnover_pct,
                      trade_status, is_st
                 FROM finance.daily_bars
                WHERE dataset_id=%s AND dataset_version=ANY(%s) AND adjust_type='raw'
                  AND source_id=%s AND security_id=%s AND trade_date<=%s
                ORDER BY trade_date DESC LIMIT %s""",
            (rising_streak.DATASET, scope['dataset_versions'],
             rising_streak.SOURCE, security_id, asof, window * 2))
        rows = cur.fetchall()
        cur.execute('SELECT code_name FROM finance.securities WHERE security_id=%s',
                    (security_id,))
        r = cur.fetchone()
        name = r[0] if r else None
    bars = [{'trade_date': str(x[0]), 'close': float(x[1]) if x[1] is not None else None,
             'pct_chg': float(x[3]) if x[3] is not None else None,
             'turnover_pct': float(x[5]) if x[5] is not None else None,
             'trade_status': x[6], 'is_st': x[7]} for x in rows]
    bars.sort(key=lambda b: b['trade_date'])
    # 去重防御：同一交易日多版本重叠时保留后写版本（scope 内理论上不重叠）
    seen, dedup = set(), []
    for b in reversed(bars):
        if b['trade_date'] not in seen:
            seen.add(b['trade_date'])
            dedup.append(b)
    bars = list(reversed(dedup))[-window:]

    d = {'security_id': security_id, 'name': name, 'asof': asof,
         'focus': focus, 'window_requested': window,
         'actual_bars': len(bars),
         'price_basis': 'raw 不复权收盘价；涨跌幅为官方 pct_chg',
         'data_window': [bars[0]['trade_date'], bars[-1]['trade_date']] if bars else None}
    if not bars:
        d['error'] = '库中无该证券数据'
        return d

    # 日历核对：窗口内应有交易日 vs 实际（日历未覆盖的整年跳过并如实标注）
    day = date.fromisoformat(bars[0]['trade_date'])
    end = date.fromisoformat(bars[-1]['trade_date'])
    required = []
    uncovered_years = []
    while day <= end:
        try:
            td = is_trading_day(day)
        except Exception:  # CalendarCoverageError：日历未覆盖该年份，整年跳过
            uncovered_years.append(day.year)
            day = date(day.year + 1, 1, 1)
            continue
        if td:
            required.append(day.isoformat())
        day += timedelta(days=1)
    have = {b['trade_date'] for b in bars}
    d['missing_days'] = [x for x in required if x not in have]
    if uncovered_years:
        d['missing_days_check'] = (f'交易日历未覆盖年份 {sorted(set(uncovered_years))}，'
                                   '这些年份的缺失日未核对')
    d['duplicate_days'] = len(rows) - len({str(x[0]) for x in rows})
    d['suspension_days'] = sum(1 for b in bars if b['trade_status'] != 1)
    d['null_field_days'] = sum(1 for b in bars if b['close'] is None or b['pct_chg'] is None)
    d['is_st_latest'] = bool(bars[-1]['is_st'])
    d['security_status'] = ('停牌中' if bars[-1]['trade_status'] != 1 else '正常交易') + \
        ('；ST' if bars[-1]['is_st'] else '')

    closes = [b['close'] for b in bars if b['close'] is not None and b['close'] > 0]
    d['last_close'] = bars[-1]['close']
    for n in (20, 60, 120, 250):
        if len(closes) > n:
            d[f'ret_{n}d_pct'] = round((closes[-1] / closes[-1 - n] - 1) * 100, 2)
    # 窗口内最大回撤（raw 收盘价）
    peak, mdd = closes[0], 0.0
    for c in closes:
        peak = max(peak, c)
        mdd = min(mdd, c / peak - 1)
    d['max_drawdown_pct'] = round(mdd * 100, 2)
    pcts = [b['pct_chg'] for b in bars[-60:] if b['pct_chg'] is not None]
    if len(pcts) > 5:
        d['volatility_60d_pct'] = round(statistics.pstdev(pcts), 3)
    turns = [b['turnover_pct'] for b in bars[-20:] if b['turnover_pct'] is not None]
    if turns:
        d['avg_turnover_20d_pct'] = round(statistics.mean(turns), 3)
    if focus:
        # 重点候选附紧凑序列（date/close/pct_chg/turnover），750 日约 15 万字符以内
        d['series'] = [{'d': b['trade_date'], 'c': b['close'], 'p': b['pct_chg'],
                        't': b['turnover_pct'], 's': b['trade_status']} for b in bars]
    return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--asof', required=True)
    ap.add_argument('--codes', required=True, help='逗号分隔 security_id')
    ap.add_argument('--focus', default='', help='逗号分隔的重点候选')
    ap.add_argument('--out-root',
                    default='/root/.openclaw/workspace/finance_p0a1_artifacts/p1a2-digest')
    args = ap.parse_args()
    focus_set = {c.strip() for c in args.focus.split(',') if c.strip()}
    dsn = rising_streak.DSN.read_text().strip()
    from finance_p0a1.baostock_current_reader import resolve_current_baostock_scope
    with psycopg.connect(dsn, connect_timeout=5) as conn:
        scope = resolve_current_baostock_scope(conn, dataset_id=rising_streak.DATASET)
        digests = {}
        for code in [c.strip() for c in args.codes.split(',') if c.strip()]:
            digests[code] = build_digest(conn, scope, code, args.asof,
                                         focus=code in focus_set)
    out = {'asof': args.asof, 'generated_at': datetime.now(CST).isoformat(),
           'dataset_versions': scope['dataset_versions'],
           'latest_trade_date': scope['latest_trade_date'],
           'reader_receipt_hash': scope['reader_receipt_hash'],
           'digests': digests}
    out['receipt_hash'] = 'sha256:' + hashlib.sha256(
        json.dumps(digests, ensure_ascii=False, sort_keys=True,
                   separators=(',', ':')).encode()).hexdigest()
    out_dir = Path(args.out_root)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f'digest_{args.asof}.json'
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding='utf-8')
    tmp.replace(path)
    for code, d in digests.items():
        print(f"{code} {d.get('name')}: bars={d.get('actual_bars')} "
              f"window={d.get('data_window')} missing={len(d.get('missing_days', []))} "
              f"dup={d.get('duplicate_days')} susp={d.get('suspension_days')} "
              f"null={d.get('null_field_days')} status={d.get('security_status')} "
              f"ret250={d.get('ret_250d_pct')} mdd={d.get('max_drawdown_pct')}")
    print('receipt:', path, out['receipt_hash'][:24])


if __name__ == '__main__':
    main()
