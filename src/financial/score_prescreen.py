#!/usr/bin/env python3
"""P0.8 脚本预评分（score_rules v1.0，冻结量表，权重不得改）。

对 rising_streak.fetch_candidates 的 verified_candidates 做纯本地脚本预评分
（零模型调用）：每票拉截至信号日最近 65 个交易日完整日线，计算 A~E 分项，
合成 script_score = A+B+C+D+E（E≤0），原子写 reports/evidence/score_prescreen_{run-date}.json。

fail-closed 纪律：任一关键字段缺失/非法（None、非有限数、close≤0、preclose≤0）
→ 该候选进 unresolved 清单（写明原因），不置零、不估算、不出分项分。

用法：python3 score_prescreen.py [--run-date 2026-10-13]
      python3 score_prescreen.py --self-test   # 合成数据 smoke，不连库
"""
import argparse
import json
import math
import statistics
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
try:
    import psycopg  # noqa: E402
    import rising_streak  # noqa: E402  复用 DATASET/SOURCE/DSN
except ImportError:  # 非 WSL 环境（无 psycopg）仅可跑 --self-test
    psycopg = None
    rising_streak = None

CST = timezone(timedelta(hours=8))
WORKSPACE = Path('/root/.openclaw/workspace')
EVIDENCE_DIR = WORKSPACE / 'reports/preopen/evidence'  # 与节点1快照、新闻归档同目录
RULE_VERSION = 'score_rules-v1.0'
BAR_WINDOW = 65  # 截至信号日最近 65 个交易日
LIMIT_UP_MAIN = 0.10
LIMIT_UP_ST = 0.05


def with_suffix(code6: str) -> str:
    return code6 + ('.SH' if code6[0] == '6' else '.SZ')


# ---------- 数据访问（照抄 p1a3_backfill 模式） ----------

def load_bars(conn, scope, security_ids, signal_date):
    """每票截至 signal_date 最近 65 个交易日的完整日线（升序）。"""
    with conn.cursor() as cur:
        cur.execute(
            """SELECT DISTINCT trade_date FROM finance.daily_bars
                WHERE dataset_id=%s AND dataset_version=ANY(%s) AND adjust_type='raw'
                  AND source_id=%s AND trade_date<=%s
                ORDER BY trade_date DESC LIMIT %s""",
            (rising_streak.DATASET, scope['dataset_versions'], rising_streak.SOURCE,
             signal_date, BAR_WINDOW))
        dates = [str(r[0]) for r in cur.fetchall()]
    if not dates:
        return {}
    bars = {}
    with conn.cursor() as cur:
        cur.execute(
            """SELECT security_id, trade_date, open, high, low, close, preclose,
                      volume, pct_chg, turnover_pct, trade_status, is_st
                 FROM finance.daily_bars
                WHERE dataset_id=%s AND dataset_version=ANY(%s) AND adjust_type='raw'
                  AND source_id=%s AND security_id=ANY(%s)
                  AND trade_date=ANY(%s::date[])
                ORDER BY security_id, trade_date""",
            (rising_streak.DATASET, scope['dataset_versions'], rising_streak.SOURCE,
             list(security_ids), dates))
        cols = [c.name for c in cur.description]
        for row in cur.fetchall():
            b = dict(zip(cols, row))
            b['trade_date'] = str(b['trade_date'])
            for k in ('open', 'high', 'low', 'close', 'preclose', 'volume',
                      'pct_chg', 'turnover_pct'):
                b[k] = float(b[k]) if b[k] is not None else None
            b['is_st'] = bool(b['is_st']) if b['is_st'] is not None else None
            bars.setdefault(b['security_id'], []).append(b)
    return bars


# ---------- 量表 score_rules v1.0 ----------

def _valid_num(v):
    return v is not None and isinstance(v, (int, float)) and not isinstance(v, bool) \
        and math.isfinite(v)


def _validate_bars(bars, days):
    """关键字段 fail-closed 校验，返回 None 或原因字符串。"""
    if len(bars) < 60:
        return f'日线不足60日（{len(bars)} 行），MA60/high60 无法计算'
    for b in bars:
        for k in ('open', 'high', 'low', 'close', 'preclose', 'volume', 'pct_chg'):
            if not _valid_num(b.get(k)):
                return f"{b.get('trade_date')} 字段 {k} 缺失或非法"
        if b['close'] <= 0 or b['preclose'] <= 0:
            return f"{b['trade_date']} close/preclose 非正（{b['close']}/{b['preclose']}）"
        if b.get('trade_status') is None:
            return f"{b.get('trade_date')} trade_status 缺失"
    for b in bars[-days:]:
        if not _valid_num(b.get('turnover_pct')):
            return f"{b['trade_date']} 连涨期换手率缺失或非法"
        if b.get('is_st') is None:
            return f"{b['trade_date']} is_st 缺失"
    return None


def score_candidate(cand, bars, signal_date):
    """单候选评分。返回 (entry, None) 或 (None, reason)；不出部分分。"""
    code, name = cand['code'], cand['name']
    sid = with_suffix(code)
    if not bars:
        return None, '信号窗口内无日线数据'
    if bars[-1]['trade_date'] != signal_date:
        return None, f"信号日 {signal_date} 无日线（末日 {bars[-1]['trade_date']}）"
    days = int(cand['days'])
    if len(bars) < days + 5:
        return None, f'日线不足（{len(bars)} 行 < 连涨{days}日+前5日均量），量比无法计算'
    reason = _validate_bars(bars, days)
    if reason:
        return None, reason

    # 连涨天数日线自验：从末日向前 pct_chg>0 且 trade_status=1 的连续天数
    actual_days = 0
    for b in reversed(bars):
        if b['trade_status'] != 1 or b['pct_chg'] <= 0:
            break
        actual_days += 1
    if actual_days != days:
        return None, f'连涨天数与日线自验不一致（candidate={days}，日线={actual_days}）'

    streak = bars[-days:]
    streak_pct = [b['pct_chg'] for b in streak]
    last = bars[-1]
    gain = float(cand['gain'])
    turnover = float(cand['turnover'])

    # A 趋势延续（上限25）
    if days == 3:
        a_base = 12
    elif days == 4:
        a_base = 16
    elif days <= 6:
        a_base = 20
    else:
        a_base = max(0, 20 - 2 * (days - 6))
    a_bonus = 5 if all(0.5 <= p <= 3.0 for p in streak_pct) else 0
    A = min(25, a_base + a_bonus)

    # B 动量结构（上限20）
    b_gain = (12 if 5 <= gain < 15 else 8 if 15 <= gain < 25
              else 6 if gain < 5 else 3)
    volatility = statistics.pstdev(streak_pct)
    b_vol = 8 if volatility < 1 else 5 if volatility < 2 else 2
    B = b_gain + b_vol

    # C 量能换手（上限20）；量比 vr = 末日量 ÷ 末日前5个交易日均量
    prev5_vol = statistics.mean(b['volume'] for b in bars[-6:-1])
    if prev5_vol <= 0:
        return None, '末日前5日均量≤0，量比无法计算'
    vr = last['volume'] / prev5_vol
    c_vr = (10 if 1.2 <= vr < 2.5 else 6 if (2.5 <= vr < 4 or 0.8 <= vr < 1.2)
            else 3 if vr < 0.8 else 2)
    c_to = 10 if 5 <= turnover < 20 else 5 if (turnover < 5 or turnover < 30) else 0
    C = c_vr + c_to

    # D 技术位置（上限20）；MA 为收盘价简单均线（含末日）
    closes = [b['close'] for b in bars]
    ma5 = statistics.mean(closes[-5:])
    ma10 = statistics.mean(closes[-10:])
    ma20 = statistics.mean(closes[-20:])
    ma60 = statistics.mean(closes[-60:])
    d_ma = (10 if ma5 > ma10 > ma20 > ma60 else
            7 if ma5 > ma10 > ma20 else 3)
    high60 = max(closes[-60:])
    dist_h60 = last['close'] / high60 - 1
    d_high = 6 if dist_h60 >= -0.05 else 4 if dist_h60 >= -0.10 else 2
    if ma20 <= 0:
        return None, 'MA20≤0，偏离度无法计算'
    dev_ma20 = last['close'] / ma20 - 1
    d_dev = 4 if dev_ma20 < 0.08 else 2 if dev_ma20 < 0.15 else 0
    D = d_ma + d_high + d_dev

    # E 技术面惩罚（≤0）
    E = 0
    big_up_day = any(p > 7 for p in streak_pct)
    if big_up_day:
        E -= 5
    limit_up_count = 0
    for b in streak:
        rate = LIMIT_UP_ST if b['is_st'] else LIMIT_UP_MAIN
        if b['close'] >= round(b['preclose'] * (1 + rate), 2):
            limit_up_count += 1
    E -= 3 * limit_up_count
    volume_upper_shadow = (
        last['volume'] >= prev5_vol * 4
        and (last['high'] - max(last['open'], last['close'])) / last['preclose'] >= 0.03)
    if volume_upper_shadow:
        E -= 5

    entry = {
        'code': code, 'security_id': sid, 'name': name,
        'days': days, 'gain_pct': round(gain, 2), 'turnover_pct': round(turnover, 2),
        'volume_ratio': round(vr, 3),
        'scores': {'A': A, 'B': B, 'C': C, 'D': D, 'E': E},
        'script_score': A + B + C + D + E,
        'indicators': {
            'ma5': round(ma5, 4), 'ma10': round(ma10, 4),
            'ma20': round(ma20, 4), 'ma60': round(ma60, 4),
            'high60': round(high60, 4),
            'dist_high60_pct': round(dist_h60 * 100, 3),
            'dev_ma20_pct': round(dev_ma20 * 100, 3),
            'volatility_pct': round(volatility, 3),
            'limit_up_count': limit_up_count,
            'big_up_day': big_up_day,
            'volume_upper_shadow': volume_upper_shadow,
        },
    }
    return entry, None


# ---------- 主流程 ----------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--run-date', default=datetime.now(CST).date().isoformat())
    ap.add_argument('--self-test', action='store_true', help='合成数据 smoke，不连库')
    args = ap.parse_args()
    if args.self_test:
        self_test()
        return
    if rising_streak is None:
        print('psycopg/rising_streak 不可用（非 WSL 环境？），仅支持 --self-test')
        sys.exit(2)
    run_date = args.run_date

    result = rising_streak.fetch_candidates(run_date)
    if not result.get('ok'):
        print(f"fetch_candidates 失败: {result.get('reason', 'unknown')}")
        sys.exit(2)
    signal_date = result['date']
    candidates = result['verified_candidates']

    from finance_p0a1.baostock_current_reader import resolve_current_baostock_scope
    dsn = rising_streak.DSN.read_text().strip()
    with psycopg.connect(dsn, connect_timeout=5) as conn:
        scope = resolve_current_baostock_scope(conn, dataset_id=rising_streak.DATASET)
        bars = load_bars(conn, scope,
                         [with_suffix(c['code']) for c in candidates], signal_date)

    scored, unresolved = [], []
    for c in candidates:
        entry, reason = score_candidate(c, bars.get(with_suffix(c['code']), []),
                                        signal_date)
        if entry:
            scored.append(entry)
        else:
            unresolved.append({'code': c['code'], 'name': c['name'],
                               'reason': reason})
    scored.sort(key=lambda e: -e['script_score'])

    payload = {
        'run_date': run_date, 'signal_date': signal_date,
        'rule_version': RULE_VERSION,
        'dataset_id': result['dataset_id'],
        'dataset_versions': scope['dataset_versions'],
        'reader_receipt_hash': scope['reader_receipt_hash'],
        'generated_at': datetime.now(CST).isoformat(),
        'candidate_count': len(candidates), 'scored_count': len(scored),
        'candidates': scored, 'unresolved': unresolved,
    }
    path = EVIDENCE_DIR / f'score_prescreen_{run_date}.json'
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding='utf-8')
    tmp.replace(path)
    print(json.dumps({'output': str(path), 'signal_date': signal_date,
                      'candidates': len(candidates), 'scored': len(scored),
                      'unresolved': len(unresolved),
                      'top3': [(e['code'], e['script_score']) for e in scored[:3]]},
                     ensure_ascii=False, indent=1))


# ---------- 合成数据 smoke ----------

def _mk_bars(pct_list, base=10.0, volume=10000.0, turnover=3.0,
             start='2026-07-01', is_st=0, limit_up_day=None):
    """按日 pct_chg 序列合成 bars（升序）。limit_up_day=索引 i 时该日收盘=涨停价。"""
    bars = []
    close = base
    day = datetime.fromisoformat(start).date()
    for i, p in enumerate(pct_list):
        preclose = close
        if limit_up_day is not None and i == limit_up_day:
            close = round(preclose * 1.10, 2)
            p = (close / preclose - 1) * 100
        else:
            close = round(preclose * (1 + p / 100), 4)
        bars.append({
            'trade_date': (day + timedelta(days=i)).isoformat(),
            'open': round(preclose * 1.001, 4),
            'high': round(max(close, preclose) * 1.005, 4),
            'low': round(min(close, preclose) * 0.995, 4),
            'close': close, 'preclose': round(preclose, 4),
            'volume': volume, 'pct_chg': p,
            'turnover_pct': turnover, 'trade_status': 1, 'is_st': is_st,
        })
    return bars


def _mk_cand(code, name, bars, days):
    streak = bars[-days:]
    gain = (math.prod(1 + b['pct_chg'] / 100 for b in streak) - 1) * 100
    turnover = sum(b['turnover_pct'] for b in streak)
    return {'code': code, 'name': name, 'days': days,
            'gain': gain, 'turnover': turnover}


def self_test():
    ok = True

    def check(label, expected, actual):
        nonlocal ok
        mark = 'OK' if expected == actual else 'FAIL'
        if expected != actual:
            ok = False
        print(f'  [{mark}] {label}: 预期 {expected} / 实际 {actual}')

    # ① 标准多头温和票：days=4，连涨期每日 pct∈[0.5,3]
    pcts1 = [0.3] * 60 + [-0.5] + [1.5, 1.0, 2.0, 1.5]
    bars1 = _mk_bars(pcts1)
    cand1 = _mk_cand('600001', '温和多头', bars1, 4)
    e1, r1 = score_candidate(cand1, bars1, bars1[-1]['trade_date'])
    assert e1 is not None, f'票①意外 unresolved: {r1}'
    print(f"票① {e1['code']} {e1['name']}: scores={e1['scores']} script_score={e1['script_score']}")
    check('A（16基础+5温和加分）', 21, e1['scores']['A'])
    check('B（gain∈[5,15)→12，vol<1→8）', 20, e1['scores']['B'])
    check('C（vr=1.0→6，换手12%→10）', 16, e1['scores']['C'])
    check('D（多头排列10+高点0%6+偏离<8%4）', 20, e1['scores']['D'])
    check('E（无惩罚）', 0, e1['scores']['E'])
    check('script_score', 77, e1['script_score'])

    # ② 含 1 次涨停 + 单日 8% 的票：days=3，E=-3-5=-8
    pcts2 = [0.2] * 61 + [-1.0] + [8.0, 10.0, 1.0]
    bars2 = _mk_bars(pcts2, limit_up_day=63)
    cand2 = _mk_cand('000002', '涨停暴烈', bars2, 3)
    e2, r2 = score_candidate(cand2, bars2, bars2[-1]['trade_date'])
    assert e2 is not None, f'票②意外 unresolved: {r2}'
    ind2 = e2['indicators']
    print(f"票② {e2['code']} {e2['name']}: scores={e2['scores']} script_score={e2['script_score']}")
    check('E（涨停1次−3 + 单日>7%−5）', -8, e2['scores']['E'])
    check('limit_up_count', 1, ind2['limit_up_count'])
    check('big_up_day', True, ind2['big_up_day'])

    # ③ 缺 turnover 的票 → unresolved
    pcts3 = [0.3] * 61 + [1.0, 1.0, 1.0]
    bars3 = _mk_bars(pcts3)
    cand3 = _mk_cand('600003', '缺换手', bars3, 3)
    bars3[-1]['turnover_pct'] = None
    e3, r3 = score_candidate(cand3, bars3, bars3[-1]['trade_date'])
    print(f"票③ {cand3['code']} {cand3['name']}: unresolved 原因={r3}")
    check('进 unresolved', True, e3 is None)
    check('原因含换手率', True, '换手' in (r3 or ''))

    print('self-test ' + ('全部通过' if ok else '存在失败'))
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
