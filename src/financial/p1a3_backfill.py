#!/usr/bin/env python3
"""P1.3 前瞻结果回填 v2：T 日候选的买入日/结算/关窗回填与统计。

口径 v2（账本 v1.18，2026-10-13 起）：
- 买入日 B = 报告日（信号日后第一个交易日），按开盘价虚拟买入；
- 涨停不可成交：开盘价 >= round(昨收×涨停幅度, 2) → unbuyable_limit_up，
  0 股不计费不进收益与守住率，单独列名单跟踪（主板 10%，ST 5%，按 is_st 区分）；
- 费用逐笔精算：佣金 max(成交×万5, 5元) 双边 + 印花税 0.05% 卖出 + 过户费 0.001% 双边；
  费率/预算/涨停幅度/整手股数全部外置到 fee_model.json（load_fee_model fail-closed 加载）；
- 单只标准仓位 3000 元：股数=floor(3000÷开盘价÷100)×100，一手即超预算强制 1 手；
- 守住双轨：毛守住 = B+1 收盘 ≥ B 开盘价；费后守住 = B+1 收盘 ≥ 保本价（逐只反解）；
- 止损盲区：B+1 最低价 < 保本价 但收盘守住 → dipped_but_held；
- 窗口：B（买入日）/ B+1（标准结算）/ B+2（关窗，边际盈亏）；
  horizon 键 T+1/T+2/T+3 相对信号日（T+1=买入日，与对照组 T+0 同日）；
- alpha 保留信号日收盘基准（策略判断力口径），与实盘 P&L（买入日开盘基准）分离；
- legacy 断点：信号日 ≤ 2026-10-13 为 legacy 档（旧口径报告解析，置信度低），
  与新口径序列隔离统计、永不合井；
- 样本纪律：框架调整建议需同时满足 ①该口径累计有效样本 ≥100 只次
  ②分桶费后守住率差 ≥15pp ③连续 3 期运行方向一致 → "样本充足，建议用户审议"；
  否则逐条标"暂存"；<20 样本噪音警告保留为更低层护栏；
- 未到期=pending，已到期无法解析=unresolved，均不置零；纯本地计算，不调用模型。

信号来源优先级：结构化回执（baostock_streak_*.json）> finance.candidates 库记录
> 旧报告 md 解析（legacy 目录 + 现行目录）。

用法：python3 p1a3_backfill.py [--run-date 2026-10-13]
"""
import argparse
import glob
import hashlib
import json
import re
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
WORKSPACE = Path('/root/.openclaw/workspace')
REPORTS_PREOPEN = WORKSPACE / 'reports/preopen'
REPORTS_LEGACY = WORKSPACE / 'reports/_legacy_20261013/preopen'
REPORTS_POSTMARKET = WORKSPACE / 'reports/postmarket'
ARTIFACTS = WORKSPACE / 'finance_p0a1_artifacts/p1a3-backfill'
MIRROR_ROOT = Path('/mnt/c/Users/smk/Desktop/niko每日金融')
HORIZONS = (1, 2, 3)  # 相对信号日：T+1=买入日B，T+2=标准结算B+1，T+3=关窗B+2
INDEX_ID = 'sh.000300'
INDEX_SOURCE = 'baostock-index-daily'
NOISE_MIN = 20
LEGACY_CUTOFF = '2026-10-13'

# P0.8 信号源（新口径序列首选，2026-10-13 起）：score_prescreen 全集 + decision Top10 标记，
# 两者按信号日配对，缺一不回退旧源（新口径权威性靠此保证）。
PRESCREEN_DIR = REPORTS_PREOPEN / 'evidence'
DECISION_DIR = REPORTS_PREOPEN / 'decision_packets'

# 四档组合账户（服务资金投入决策）：3 万=实盘口径，5/10/15 万为仓位摩擦对照；
# 等额十分仓（每只股票预算=档位÷10），总额超限从末位 rank 逐只放弃（skipped_funds）。
PORTFOLIO_TIERS = (30000, 50000, 100000, 150000)

# 费用与仓位参数外置（2026-10-13 起）：不再硬编码常量，统一从 fee_model.json 加载，
# 缺字段/非法值 fail-closed 抛 ValueError，不许静默用默认值。
DEFAULT_FEE_MODEL_PATH = HERE / 'fee_model.json'

# 必须存在且为正数的数值字段（含仓位预算、费率、涨停幅度、整手股数）
FEE_NUMERIC_FIELDS = ('per_stock_budget', 'commission_rate', 'commission_min',
                      'stamp_rate_sell', 'transfer_rate',
                      'limit_up_rate_main', 'limit_up_rate_st', 'lot_size')


def load_fee_model(path=None) -> dict:
    """加载并校验费用模型 JSON。

    path 为 None 时默认读脚本同目录的 fee_model.json。
    校验：rule_version 非空字符串；八个数值字段全部存在、为数值且为正数。
    任何缺字段/非法值直接抛 ValueError（fail-closed，不回落默认值）。
    """
    fee_path = Path(path) if path is not None else DEFAULT_FEE_MODEL_PATH
    try:
        data = json.loads(fee_path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        raise ValueError(f'费用模型文件不存在: {fee_path}')
    except json.JSONDecodeError as exc:
        raise ValueError(f'费用模型 JSON 解析失败: {fee_path}: {exc}')
    if not isinstance(data, dict):
        raise ValueError(f'费用模型顶层必须是对象: {fee_path}')
    if not isinstance(data.get('rule_version'), str) or not data['rule_version'].strip():
        raise ValueError(f'费用模型 rule_version 缺失或为空: {fee_path}')
    for field in FEE_NUMERIC_FIELDS:
        value = data.get(field)
        # bool 是 int 子类，必须显式排除，防止 true/false 被当成 1/0 通过校验
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f'费用模型字段 {field} 缺失或不是数值: {fee_path}')
        if not value > 0:
            raise ValueError(f'费用模型字段 {field} 必须为正数（当前 {value}）: {fee_path}')
    return data


SUGGEST_MIN_SAMPLES = 100  # 样本纪律阈值
SUGGEST_MIN_SPREAD = 15.0  # 分桶费后守住率差阈值（pp）


def with_suffix(code6: str) -> str:
    return code6 + ('.SH' if code6[0] == '6' else '.SZ')


def prev_trading_day(d: date) -> date:
    d -= timedelta(days=1)
    while not is_trading_day(d):
        d -= timedelta(days=1)
    return d


def next_trading_days(start_iso: str, n: int) -> list:
    d = date.fromisoformat(start_iso)
    out = []
    guard = 0
    while len(out) < n and guard < 40:
        d += timedelta(days=1)
        guard += 1
        if is_trading_day(d):
            out.append(d.isoformat())
    return out


# ---------- 仓位与费用（v2） ----------

def buy_plan(open_price: float, fee: dict) -> dict:
    """单只标准仓位：按预算整手买入，一手即超预算强制 1 手。参数全部取自 fee。"""
    lot = int(fee['lot_size'])
    shares = int(fee['per_stock_budget'] // (open_price * lot)) * lot
    if shares == 0:
        shares = lot
    amount = open_price * shares
    commission = max(amount * fee['commission_rate'], fee['commission_min'])
    transfer = amount * fee['transfer_rate']
    return {'shares': shares, 'buy_amount': round(amount, 2),
            'commission_buy': round(commission, 2),
            'transfer_buy': round(transfer, 4),
            'buy_cost': round(amount + commission + transfer, 2)}


def sell_net(price: float, shares: int, fee: dict) -> float:
    """按 price 虚拟卖出的净所得（扣佣金/印花税/过户费）。费率全部取自 fee。"""
    proceeds = price * shares
    commission = max(proceeds * fee['commission_rate'], fee['commission_min'])
    stamp = proceeds * fee['stamp_rate_sell']
    transfer = proceeds * fee['transfer_rate']
    return proceeds - commission - stamp - transfer


def breakeven_price(open_price: float, shares: int, buy_cost: float, fee: dict) -> float:
    """保本卖出价：使卖出净所得 = 买入总成本的每股价格（二分反解，
    最低 5 元佣金的 max() 拐点导致它不是固定系数，必须逐只算）。"""
    lo, hi = open_price * 0.5, open_price * 3.0
    for _ in range(80):
        mid = (lo + hi) / 2
        if sell_net(mid, shares, fee) < buy_cost:
            lo = mid
        else:
            hi = mid
    return round((lo + hi) / 2, 4)


# ---------- 信号收集 ----------

def collect_from_p0a8():
    """P0.8 产物对（新口径序列权威信号源）：
    score_prescreen_{D}.json 提供候选池全集（含 script_score），
    decision_{D}.json（按 data_date 配对信号日）提供 Top10 rank 标记。
    返回 ({signal_date: signal}, defects)。只找到其一 → defect，不回退旧源。
    legacy 断点及之前的信号日本函数直接跳过（由旧源负责）。
    """
    signals, defects = {}, []
    decisions = {}
    for path in sorted(DECISION_DIR.glob('decision_*.json')):
        try:
            d = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError) as exc:
            defects.append(f'decision 不可解析: {path.name}（{exc}）')
            continue
        dd = d.get('data_date')
        if dd:
            decisions[dd] = (path, d)
    paired = set()
    for path in sorted(PRESCREEN_DIR.glob('score_prescreen_*.json')):
        try:
            p = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError) as exc:
            defects.append(f'score_prescreen 不可解析: {path.name}（{exc}）')
            continue
        sd = p.get('signal_date')
        if not sd:
            defects.append(f'score_prescreen 缺 signal_date: {path.name}')
            continue
        if sd <= LEGACY_CUTOFF:
            continue
        if sd not in decisions:
            defects.append(f'信号日 {sd} 有 score_prescreen 但缺配对 decision JSON')
            continue
        dpath, d = decisions[sd]
        paired.add(sd)
        rank_map = {c['code']: c['rank'] for c in (d.get('candidates') or [])}
        entries = [{'code': with_suffix(c['code']), 'name': c.get('name'),
                    'days': c['days'], 'score': c.get('script_score'),
                    'in_top10': c['code'] in rank_map, 'rank': rank_map.get(c['code'])}
                   for c in (p.get('candidates') or [])]
        signals[sd] = {'report_date': d.get('trade_date'), 'candidates': entries,
                       'source': 'p0a8_decision', 'decision': str(dpath),
                       'prescreen': str(path), 'decision_payload': d}
    for sd in decisions:
        if sd > LEGACY_CUTOFF and sd not in paired:
            defects.append(f'信号日 {sd} 有 decision 但缺配对 score_prescreen')
    return signals, defects


def collect_from_receipts():
    """结构化回执：按 asof 去重（保留最早 report_date），返回 {asof: {report_date, candidates}}"""
    per_asof = {}
    for path in sorted(glob.glob(str(REPORTS_PREOPEN / 'source_receipts/baostock_streak_*_asof_*.json'))):
        m = re.search(r'baostock_streak_(\d{4}-\d{2}-\d{2})_asof_(\d{4}-\d{2}-\d{2})', path)
        if not m:
            continue
        report_date, asof = m.groups()
        data = json.loads(Path(path).read_text(encoding='utf-8'))
        if not data.get('ok'):
            continue
        cand = data.get('verified_candidates') or data.get('top') or []
        top_codes = {t['code'] for t in (data.get('top') or [])}
        entries = [{'code': with_suffix(c['code']), 'name': c['name'],
                    'days': c['days'], 'score': c.get('score'),
                    'in_top10': c['code'] in top_codes} for c in cand]
        if asof not in per_asof or report_date < per_asof[asof]['report_date']:
            per_asof[asof] = {'report_date': report_date, 'candidates': entries,
                              'source': 'structured_receipt', 'receipt': path}
    return per_asof


def collect_from_db(conn):
    """09-29 库记录候选（旧管线 run）。"""
    with conn.cursor() as cur:
        cur.execute(
            """SELECT symbol, name, consecutive_up_days FROM finance.candidates
                WHERE run_id='finance-p0a1-real-20260929-runner-v3'""")
        rows = cur.fetchall()
    if not rows:
        return None
    return {'report_date': '2026-09-29',
            'candidates': [{'code': r[0], 'name': r[1], 'days': r[2],
                            'score': None, 'in_top10': i < 10}
                           for i, r in enumerate(rows)],
            'source': 'db_candidates'}


def collect_from_md(covered_dates):
    """旧报告 md 解析（仅顶层 Top10 表；legacy 目录 + 现行目录）。返回 {report_date: {...}}"""
    out = {}
    paths = sorted(REPORTS_LEGACY.glob('preopen_*.md')) + sorted(REPORTS_PREOPEN.glob('preopen_*.md'))
    for path in paths:
        m = re.search(r'preopen_(\d{4}-\d{2}-\d{2})\.md', path.name)
        if not m:
            continue
        report_date = m.group(1)
        if report_date in covered_dates or report_date in out:
            continue
        text = path.read_text(encoding='utf-8')
        rows = re.findall(r'^\|\s*\d+\s*\|\s*([^|（\n]+)（(\d{6})）\s*\|\s*[\d.]+\s*\|\s*(\d+)\s*天',
                          text, re.M)
        if not rows:
            out[report_date] = {'parse_status': 'no_streak_table', 'candidates': []}
            continue
        signal_date = prev_trading_day(date.fromisoformat(report_date)).isoformat()
        out[report_date] = {
            'parse_status': 'ok', 'signal_date': signal_date,
            'candidates': [{'code': with_suffix(c), 'name': n.strip(),
                            'days': int(d), 'score': None, 'in_top10': True}
                           for n, c, d in rows],
            'source': 'md_legacy_parse'}
    return out


# ---------- 结果回填 ----------

def load_bars(conn, scope, codes, date_min, date_max):
    bars = {}
    with conn.cursor() as cur:
        cur.execute(
            """SELECT security_id, trade_date, open, low, close, preclose, trade_status, is_st
                 FROM finance.daily_bars
                WHERE dataset_id=%s AND dataset_version=ANY(%s) AND adjust_type='raw'
                  AND source_id=%s AND security_id=ANY(%s)
                  AND trade_date BETWEEN %s AND %s
                ORDER BY security_id, trade_date""",
            (rising_streak.DATASET, scope['dataset_versions'], rising_streak.SOURCE,
             list(codes), date_min, date_max))
        for sid, td, o, lo, close, preclose, ts, is_st in cur.fetchall():
            bars.setdefault(sid, {})[str(td)] = {
                'open': float(o) if o is not None else None,
                'low': float(lo) if lo is not None else None,
                'close': float(close) if close is not None else None,
                'preclose': float(preclose) if preclose is not None else None,
                'trade_status': ts, 'is_st': bool(is_st)}
    return bars


def load_index(conn):
    with conn.cursor() as cur:
        cur.execute(
            """SELECT trade_date, close FROM finance.index_daily_bars
                WHERE index_id=%s AND source_id=%s ORDER BY trade_date""",
            (INDEX_ID, INDEX_SOURCE))
        return {str(r[0]): float(r[1]) for r in cur.fetchall() if r[1] is not None}


def backfill_candidate(c, signal_date, horizons_dates, bars, index, latest, fee):
    """单候选回填 v2。res['horizons']: T+1=买入日，T+2=标准结算，T+3=关窗。
    费用与涨停幅度全部取自 fee（fee_model.json 加载结果）。"""
    cbars = bars.get(c['code'], {})
    res = {'code': c['code'], 'name': c['name'], 'days': c['days'],
           'score': c.get('score'), 'in_top10': c['in_top10'],
           'signal_date': signal_date, 'horizons': {}}
    sig_bar = cbars.get(signal_date)
    sig_close = sig_bar['close'] if sig_bar and sig_bar['trade_status'] == 1 else None
    idx_entry = index.get(signal_date)

    # ---- T+1：买入日 B ----
    b_date = horizons_dates[1]
    buy = {'observation_date': b_date}
    buy_state = None  # bought / unbuyable_limit_up / unresolved / pending
    if b_date > latest:
        buy['status'] = 'pending'
        buy_state = 'pending'
    elif sig_close is None:
        buy['status'] = 'unresolved'
        buy['reason'] = '信号日无有效收盘（停牌或缺数据）'
        buy_state = 'unresolved'
    else:
        bbar = cbars.get(b_date)
        if not bbar or bbar['trade_status'] != 1 or not bbar['open']:
            buy['status'] = 'unresolved'
            buy['reason'] = '买入日停牌或缺数据'
            buy_state = 'unresolved'
        else:
            o = bbar['open']
            prev_close = bbar['preclose'] or sig_close
            limit_rate = fee['limit_up_rate_st'] if bbar['is_st'] else fee['limit_up_rate_main']
            limit_up = round(prev_close * limit_rate, 2)
            buy['open'] = o
            buy['prev_close'] = prev_close
            buy['limit_up_price'] = limit_up
            if o >= limit_up:
                buy['status'] = 'unbuyable_limit_up'
                buy_state = 'unbuyable_limit_up'
            else:
                plan = buy_plan(o, fee)
                be = breakeven_price(o, plan['shares'], plan['buy_cost'], fee)
                buy.update(plan)
                buy['breakeven_price'] = be
                buy['status'] = 'bought'
                buy_state = 'bought'
                if bbar['close']:
                    buy['close'] = bbar['close']
                    buy['gross_return_pct'] = round((bbar['close'] / o - 1) * 100, 3)
    res['horizons']['T+1'] = buy

    # ---- T+2 标准结算 / T+3 关窗 ----
    for h in (2, 3):
        hdate = horizons_dates[h]
        cell = {'observation_date': hdate}
        if hdate > latest:
            cell['status'] = 'pending'
        elif buy_state == 'pending':
            cell['status'] = 'pending'
        elif buy_state == 'unbuyable_limit_up':
            cell['status'] = 'unbuyable_limit_up'
            eb = cbars.get(hdate)
            if eb and eb['close'] and sig_close:
                cell['watch_return_pct'] = round((eb['close'] / sig_close - 1) * 100, 3)
        elif buy_state == 'unresolved':
            cell['status'] = 'unresolved'
            cell['reason'] = '买入日未成交（见 T+1）'
        else:
            eb = cbars.get(hdate)
            if not eb or eb['trade_status'] != 1 or not eb['close']:
                cell['status'] = 'unresolved'
                cell['reason'] = '观察日停牌或缺数据'
            else:
                exit_c = eb['close']
                net = sell_net(exit_c, buy['shares'], fee)
                cell['status'] = 'filled'
                cell['exit_close'] = exit_c
                cell['net_pnl'] = round(net - buy['buy_cost'], 2)
                cell['net_return_pct'] = round((net / buy['buy_cost'] - 1) * 100, 3)
                cell['gross_return_pct'] = round((exit_c / buy['open'] - 1) * 100, 3)
                if h == 2:
                    cell['held_gross'] = exit_c >= buy['open']
                    cell['held_net'] = exit_c >= buy['breakeven_price']
                    if eb.get('low') is not None:
                        cell['dipped_below_breakeven'] = eb['low'] < buy['breakeven_price']
                        cell['max_intraday_dd_pct'] = round((eb['low'] / buy['open'] - 1) * 100, 3)
                if idx_entry and index.get(hdate):
                    idx_ret = index[hdate] / idx_entry - 1
                    cell['index_return_pct'] = round(idx_ret * 100, 3)
                    # alpha 保留信号日收盘基准（策略判断力口径）
                    cell['alpha_pct'] = round(((exit_c / sig_close - 1) - idx_ret) * 100, 3)
                else:
                    cell['alpha_pct'] = None
        res['horizons'][f'T+{h}'] = cell

    # T+3 边际盈亏 = T+3 净盈亏 − T+2 净盈亏
    t2, t3 = res['horizons']['T+2'], res['horizons']['T+3']
    if t2.get('status') == 'filled' and t3.get('status') == 'filled':
        t3['marginal_pnl_vs_t2'] = round(t3['net_pnl'] - t2['net_pnl'], 2)
    return res


# ---------- 四档组合账户（新口径序列专属） ----------

def build_portfolio_tiers(signals, bars, index, latest, fee):
    """对每个新口径信号日（p0a8_decision 源）的 Top10 按四档预算模拟组合。

    规则：等额十分仓（单只预算=档位÷10，经 buy_plan 整手取整，一手即超预算强制 1 手）；
    总额（含费用）超档位 → 从末位 rank 逐只放弃（skipped_funds）；
    涨停不可成交/停牌 0 股照列不占预算；Top10 不足 10 只时空槽预算计入闲置。
    结算：T+2（B+1 收盘费后净额）与 T+3（关窗）双窗口，超额 vs 沪深300 同窗。
    未到期 → status pending（只有买入侧或全无）。
    """
    portfolios = {}
    for sd, s in sorted(signals.items()):
        if s['source'] != 'p0a8_decision':
            continue
        hd = {h: d for h, d in zip(HORIZONS, next_trading_days(sd, max(HORIZONS)))}
        b_date = hd[1]
        top10 = sorted((c for c in s['candidates'] if c['in_top10']),
                       key=lambda c: c['rank'])
        idx_entry = index.get(sd)
        tier_reports = []
        for tier in PORTFOLIO_TIERS:
            tfee = dict(fee, per_stock_budget=tier / 10)
            positions = []
            for c in top10:
                pos = {'rank': c['rank'], 'code': c['code'], 'name': c['name']}
                cbars = bars.get(c['code'], {})
                if b_date > latest:
                    pos['status'] = 'pending'
                else:
                    bbar = cbars.get(b_date)
                    if not bbar or bbar['trade_status'] != 1 or not bbar['open']:
                        pos['status'] = 'unresolved'
                        pos['reason'] = '买入日停牌或缺数据'
                    else:
                        o = bbar['open']
                        prev_close = bbar['preclose'] or (cbars.get(sd) or {}).get('close')
                        limit_rate = (fee['limit_up_rate_st'] if bbar['is_st']
                                      else fee['limit_up_rate_main'])
                        if prev_close and o >= round(prev_close * limit_rate, 2):
                            pos['status'] = 'unbuyable_limit_up'
                        else:
                            plan = buy_plan(o, tfee)
                            pos.update(plan)
                            pos['open'] = o
                            pos['status'] = 'bought'
                positions.append(pos)
            # 总额校验：从末位 rank 逐只放弃直到不超额
            skipped = []
            bought = [p for p in positions if p['status'] == 'bought']
            while bought and sum(p['buy_cost'] for p in bought) > tier + 1e-9:
                drop = bought.pop()  # positions 按 rank 升序，末位即最大 rank
                drop['status'] = 'skipped_funds'
                skipped.append({'rank': drop['rank'], 'code': drop['code'],
                                'reason': '组合总额超限，末位放弃'})
            invested = round(sum(p['buy_cost'] for p in bought), 2)
            idle = round(tier - invested, 2)
            rep = {'tier': tier, 'budget_per_stock': tier / 10,
                   'positions': positions, 'skipped_funds': skipped,
                   'accepted': len(bought), 'total_invested': invested,
                   'idle_cash': idle, 'windows': {}}
            # 结算窗口：T+2 / T+3
            if any(p['status'] == 'pending' for p in positions) or b_date > latest:
                rep['status'] = 'pending'
            else:
                rep['status'] = 'bought_pending_settle'
                for h, hdate in ((2, hd[2]), (3, hd[3])):
                    if hdate > latest:
                        rep['windows'][f'T+{h}'] = {'status': 'pending'}
                        continue
                    pnl = 0.0
                    settled = 0
                    for p in bought:
                        eb = (bars.get(p['code']) or {}).get(hdate)
                        if not eb or eb['trade_status'] != 1 or not eb['close']:
                            continue
                        pnl += sell_net(eb['close'], p['shares'], tfee) - p['buy_cost']
                        settled += 1
                    if settled < len(bought):
                        rep['windows'][f'T+{h}'] = {'status': 'unresolved',
                                                    'reason': f'{len(bought) - settled} 只观察日缺数据'}
                        continue
                    w = {'status': 'filled', 'net_pnl': round(pnl, 2)}
                    if invested > 0:
                        w['return_on_invested_pct'] = round(pnl / invested * 100, 3)
                        w['return_on_tier_pct'] = round(pnl / tier * 100, 3)
                    if idx_entry and index.get(hdate):
                        idx_ret = (index[hdate] / idx_entry - 1) * 100
                        w['index_return_pct'] = round(idx_ret, 3)
                        w['alpha_on_tier_pp'] = round(pnl / tier * 100 - idx_ret, 3)
                    rep['windows'][f'T+{h}'] = w
                if rep['windows'].get('T+2', {}).get('status') == 'filled':
                    rep['status'] = 'filled'
            tier_reports.append(rep)
        portfolios[sd] = {'report_date': s['report_date'],
                          'top10_count': len(top10), 'tiers': tier_reports}
    return portfolios


# ---------- 统计 ----------

def bucket_of(days):
    if days is None:
        return 'unknown'
    if days <= 4:
        return '3-4'
    if days <= 6:
        return '5-6'
    return '>=7'


def stats_of(samples, hkey):
    cells = [(s, s['horizons'][hkey]) for s in samples]
    # T+1 买入日的成交态是 bought，与 filled 同计入"有效样本"
    filled = [(s, c) for s, c in cells if c.get('status') in ('filled', 'bought')]
    out = {'filled': len(filled),
           'pending': sum(1 for _, c in cells if c.get('status') == 'pending'),
           'unresolved': sum(1 for _, c in cells if c.get('status') == 'unresolved'),
           'unbuyable_limit_up': sum(1 for _, c in cells
                                     if c.get('status') == 'unbuyable_limit_up')}
    if filled:
        gross = [c['gross_return_pct'] for _, c in filled
                 if c.get('gross_return_pct') is not None]
        net = [c['net_return_pct'] for _, c in filled
               if c.get('net_return_pct') is not None]
        alphas = [c['alpha_pct'] for _, c in filled if c.get('alpha_pct') is not None]
        if gross:
            out['gross_mean_pct'] = round(statistics.mean(gross), 3)
            out['win_rate_gross'] = round(sum(1 for g in gross if g > 0) / len(gross), 3)
        if net:
            out['net_return_mean_pct'] = round(statistics.mean(net), 3)
            out['net_pnl_sum'] = round(sum(c['net_pnl'] for _, c in filled
                                           if c.get('net_pnl') is not None), 2)
            out['win_rate_net'] = round(sum(1 for n in net if n > 0) / len(net), 3)
        if alphas:
            out['alpha_mean_pct'] = round(statistics.mean(alphas), 3)
            out['win_rate_alpha'] = round(sum(1 for a in alphas if a > 0) / len(alphas), 3)
        if hkey == 'T+2':
            out['held_rate_gross'] = round(
                sum(1 for _, c in filled if c.get('held_gross')) / len(filled), 3)
            out['held_rate_net'] = round(
                sum(1 for _, c in filled if c.get('held_net')) / len(filled), 3)
            dips = [c for _, c in filled if c.get('dipped_below_breakeven') and c.get('held_net')]
            out['dip_but_held'] = len(dips)
            out['dip_but_held_rate'] = round(len(dips) / len(filled), 3)
            dds = [c['max_intraday_dd_pct'] for _, c in filled
                   if c.get('max_intraday_dd_pct') is not None]
            if dds:
                out['worst_intraday_dd_pct'] = round(min(dds), 3)
        if hkey == 'T+3':
            marg = [c['marginal_pnl_vs_t2'] for _, c in filled
                    if c.get('marginal_pnl_vs_t2') is not None]
            if marg:
                out['marginal_pnl_mean_vs_t2'] = round(statistics.mean(marg), 2)
                out['t3_better_rate'] = round(sum(1 for m in marg if m > 0) / len(marg), 3)
        if len(filled) < NOISE_MIN:
            out['noise_warning'] = (f'有方向已回填样本 {len(filled)} 条 < {NOISE_MIN}，'
                                    '当前样本量下比率基本是噪音')
    return out


def compute_block(results):
    """一组候选的完整统计块（分 horizon / 分桶 / 分组）。"""
    block = {'stats': {f'T+{h}': stats_of(results, f'T+{h}') for h in HORIZONS},
             'total': len(results)}
    buckets = {}
    for b in ('3-4', '5-6', '>=7'):
        sub = [r for r in results if r['bucket'] == b]
        buckets[b] = {f'T+{h}': stats_of(sub, f'T+{h}') for h in HORIZONS}
        buckets[b]['total'] = len(sub)
    block['buckets'] = buckets
    groups = {}
    for gname, pred in (('niko_top10', lambda r: r['in_top10']),
                        ('non_top10', lambda r: not r['in_top10']),
                        ('overlap', lambda r: r['cmp_group'] == 'overlap'),
                        ('local_only', lambda r: r['cmp_group'] == 'local_only')):
        sub = [r for r in results if pred(r)]
        if sub:
            groups[gname] = {f'T+{h}': stats_of(sub, f'T+{h}') for h in HORIZONS}
            groups[gname]['total'] = len(sub)
    block['groups'] = groups
    return block


# ---------- 样本纪律与暂存建议（v2） ----------

def load_bucket_history():
    path = ARTIFACTS / 'bucket_history.jsonl'
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding='utf-8').splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def build_suggestions(run_date, new_block, history):
    """评分框架调整建议（暂存清单）。纪律：≥100 只次 + 分桶差≥15pp + 连续 3 期方向一致。"""
    suggestions = []
    buckets = new_block['buckets']
    t2 = {b: buckets[b]['T+2'] for b in ('3-4', '5-6', '>=7')}
    rates = {b: t2[b].get('held_rate_net') for b in t2}
    samples = sum(t2[b]['filled'] for b in t2)
    entry = {'run_date': run_date, 'samples_t2': samples,
             'held_net_by_bucket': rates}
    if all(v is not None for v in rates.values()):
        spread = round((rates['3-4'] - rates['>=7']) * 100, 1)  # pp，正=长连涨更危险
        entry['spread_pp'] = spread
        direction = 'longer_more_dangerous' if spread > 0 else 'longer_safer_or_flat'
        recent = [h for h in history if h.get('direction') == direction]
        consistent_3 = len(recent) >= 2  # 加本期即连续 3 期
        enough = samples >= SUGGEST_MIN_SAMPLES and abs(spread) >= SUGGEST_MIN_SPREAD and consistent_3
        suggestions.append({
            'topic': '连涨天数越长 T+1 越危险（A 维度调整依据）',
            'evidence': {'samples_t2': samples,
                         'held_rate_net': rates,
                         'spread_3-4_minus_>=7_pp': spread,
                         'direction': direction,
                         'direction_streak_incl_today': len(recent) + 1},
            'thresholds': {'min_samples': SUGGEST_MIN_SAMPLES,
                           'min_spread_pp': SUGGEST_MIN_SPREAD,
                           'min_streak': 3},
            'status': '样本充足，建议用户审议' if enough else '样本不足，暂存'})
    entry['direction'] = ('longer_more_dangerous' if entry.get('spread_pp', 0) > 0
                          else 'longer_safer_or_flat') if 'spread_pp' in entry else None
    # 涨停不可成交专项（对照组口径：单期 ≥3/10 或连续两期 ≥2/10 → 提示选股过于暴烈）
    t1 = new_block['stats']['T+1']
    total_actionable = t1['filled'] + t1['unbuyable_limit_up']
    if total_actionable:
        entry['unbuyable_t1'] = t1['unbuyable_limit_up']
        if t1['unbuyable_limit_up']:
            suggestions.append({
                'topic': '涨停不可成交占比（选股暴烈度）',
                'evidence': {'unbuyable_limit_up': t1['unbuyable_limit_up'],
                             'note': '候选级口径，非 Top10 口径；Top10 口径待 P0.8 落地'},
                'status': '暂存（待 Top10 口径）'})
    return suggestions, entry


# ---------- 报告 ----------

def fmt_stat(s):
    base = f"填 {s['filled']} / 待 {s['pending']} / 异 {s['unresolved']}"
    if s.get('unbuyable_limit_up'):
        base += f" / 涨停不可成交 {s['unbuyable_limit_up']}"
    if s['filled']:
        if 'net_return_mean_pct' in s:
            base += (f"；净收益均值 {s['net_return_mean_pct']}%（费后），净胜率 {s['win_rate_net']}，"
                     f"净盈亏合计 {s['net_pnl_sum']} 元")
        elif 'gross_mean_pct' in s:
            base += f"；毛收益均值 {s['gross_mean_pct']}%，毛胜率 {s['win_rate_gross']}"
        if 'alpha_mean_pct' in s:
            base += f"，alpha 均值 {s['alpha_mean_pct']}%，alpha 胜率 {s['win_rate_alpha']}"
        if 'held_rate_net' in s:
            base += (f"，守住率 毛 {s['held_rate_gross']} / 费后 {s['held_rate_net']}")
        if 'dip_but_held' in s:
            base += f"，盘中跌破保本价但收盘守住 {s['dip_but_held']}（{s['dip_but_held_rate']}）"
        if 'marginal_pnl_mean_vs_t2' in s:
            base += (f"，T+3 边际盈亏均值 {s['marginal_pnl_mean_vs_t2']} 元，"
                     f"多拿一天更优比例 {s['t3_better_rate']}")
    if 'noise_warning' in s:
        base += f" ⚠️{s['noise_warning']}"
    return base


def write_block_report(L, title, block):
    L += ['', f'## {title}', '']
    for h in ('T+1', 'T+2', 'T+3'):
        label = {'T+1': '买入日', 'T+2': '标准结算', 'T+3': '关窗'}[h]
        L.append(f"- **{h}（{label}）**：{fmt_stat(block['stats'][h])}")
    L += ['', '### 连涨天数分桶', '',
          '| 桶 | 样本 | 费后守住率 | 净收益均值 | alpha均值 | 备注 |',
          '|---|---:|---:|---:|---:|---|']
    for b in ('3-4', '5-6', '>=7'):
        s = block['buckets'][b]
        t2 = s['T+2']
        note = '⚠️噪音' if t2.get('noise_warning') else ''
        L.append(f"| {b} 天 | {s['total']} | {t2.get('held_rate_net', '-')} | "
                 f"{t2.get('net_return_mean_pct', '-')} | {t2.get('alpha_mean_pct', '-')} | {note} |")
    if block['groups']:
        L += ['', '### 分组统计', '']
        for g, s in block['groups'].items():
            L.append(f"- **{g}**（{s['total']} 只）：T+2 {fmt_stat(s['T+2'])}")


def write_report(receipt, path):
    L = [f"# 前瞻结果回填 v2（运行日 {receipt['run_date']}）", '',
         f"> 数据截至 {receipt['latest_trade_date']} 收盘（current reader 已验证批次 "
         f"{', '.join(receipt['dataset_versions'])}）；alpha 基准 = 沪深300"
         f"（{receipt['index']['id']}，{receipt['index']['rows']} 行）。",
         '> 口径 v2：买入日开盘虚拟买入（3000 元/只标准仓位），费用逐笔精算，'
         '守住率毛/费后双轨；alpha 保留信号日收盘基准。',
         '> 纯本地计算，未调用模型。pending=未到期，unresolved=已到期无法解析，'
         'unbuyable_limit_up=涨停不可成交（单独跟踪不进统计）。',
         f"> legacy 断点：信号日 ≤ {LEGACY_CUTOFF} 为旧口径档，隔离统计不合井。",
         f"> 回执哈希 `{receipt['receipt_hash']}`。", '',
         '## 信号覆盖', '']
    for sd, s in receipt['signal_dates'].items():
        tag = 'legacy' if sd <= LEGACY_CUTOFF else 'new'
        L.append(f"- 信号日 {sd}（报告 {s['report_date']}，{s['source']}，{tag}）：{s['count']} 只")
    if receipt['md_parse_failures']:
        L.append(f"- ⚠️ 旧报告解析无候选表：{receipt['md_parse_failures']}")
    if receipt.get('signal_defects'):
        L.append(f"- ⚠️ 新口径信号缺陷：{receipt['signal_defects']}")

    # 四档组合账户（新口径序列专属；服务资金投入决策）
    pf = receipt.get('portfolio_tiers') or {}
    if pf:
        L += ['', '## 四档组合账户（等额十分仓，费用逐笔精算）', '',
              '> 3 万档=实盘口径，其余为仓位摩擦对照；总额超限从末位放弃（skipped_funds）；',
              '> 涨停不可成交 0 股不占预算；收益率% 两个口径：占实际投入 / 占档位总额（含闲置）。', '']
        for sd, blk in pf.items():
            L.append(f"### 信号日 {sd}（Top{blk['top10_count']}）")
            L += ['', '| 档位 | 成交 | 总投入 | 闲置现金 | skipped | T+2费后净收益 | 收益率(投入/档位) | 超额pp | 状态 |',
                  '|---|---:|---:|---:|---:|---:|---|---:|---|']
            for t in blk['tiers']:
                w2 = t['windows'].get('T+2') or {}
                if w2.get('status') == 'filled':
                    pnl = f"{w2['net_pnl']:+.2f}"
                    ret = f"{w2.get('return_on_invested_pct', '-')}%/{w2.get('return_on_tier_pct', '-')}%"
                    alpha = w2.get('alpha_on_tier_pp', '-')
                else:
                    pnl = ret = alpha = '-'
                L.append(f"| {t['tier']//10000}万 | {t['accepted']} | {t['total_invested']:.0f} "
                         f"| {t['idle_cash']:.0f} | {len(t['skipped_funds'])} | {pnl} "
                         f"| {ret} | {alpha} | {t['status']} |")
                for sk in t['skipped_funds']:
                    L.append(f"| （{t['tier']//10000}万 skipped） | | | | "
                             f"rank{sk['rank']} {sk['code']} | | | | |")
            L.append('')

    for calib, title in (('new', '新口径序列（可靠）'), ('legacy', 'legacy 档（旧口径，仅供方向性参考）')):
        block = receipt['calibers'].get(calib)
        if block and block['total']:
            write_block_report(L, f'{title}（{block["total"]} 只）', block)

    unbuy = [r for r in receipt['results']
             if r['horizons']['T+1'].get('status') == 'unbuyable_limit_up']
    if unbuy:
        L += ['', '## 涨停不可成交名单（跟踪不进统计）', '',
              '| 信号日 | 代码 | 名称 | 开盘价 | 涨停价 | T+3 观察收益 |',
              '|---|---|---|---:|---:|---:|']
        for r in unbuy:
            t1, t3 = r['horizons']['T+1'], r['horizons']['T+3']
            L.append(f"| {r['signal_date']} | {r['code']} | {r['name']} | {t1['open']} | "
                     f"{t1['limit_up_price']} | {t3.get('watch_return_pct', '-')} |")

    if receipt.get('suggestions'):
        L += ['', '## 评分框架调整建议（暂存清单）', '']
        for s in receipt['suggestions']:
            L.append(f"- **{s['topic']}**——{s['status']}；证据 `{json.dumps(s['evidence'], ensure_ascii=False)}`")
        L.append('> 改不改永远由用户拍板，任务无权自行改框架。')

    L += ['', '## 明细', '',
          '| 信号日 | 档 | 代码 | 名称 | 连涨 | Top10 | 买入 | 保本价 | T+2净收益 | T+2守住 | T+3净收益 |',
          '|---|---|---|---|---:|---|---|---:|---:|---|---:|']
    for r in receipt['results']:
        t1 = r['horizons']['T+1']
        t2 = r['horizons']['T+2']
        t3 = r['horizons']['T+3']
        buy_desc = t1['status'] if t1['status'] != 'bought' else f"{t1['open']}×{t1['shares']}"
        be = f"{t1['breakeven_price']}" if t1.get('breakeven_price') else '-'
        t2_desc = (f"{t2['net_return_pct']}%" if t2.get('status') == 'filled'
                   else t2['status'])
        held = ('毛✓费✓' if t2.get('held_gross') and t2.get('held_net')
                else '毛✓费✗' if t2.get('held_gross')
                else '✗' if t2.get('status') == 'filled' else '-')
        t3_desc = (f"{t3['net_return_pct']}%" if t3.get('status') == 'filled'
                   else t3['status'])
        L.append(f"| {r['signal_date']} | {r['caliber']} | {r['code']} | {r['name']} | "
                 f"{r['days']} | {'是' if r['in_top10'] else '否'} | {buy_desc} | {be} | "
                 f"{t2_desc} | {held} | {t3_desc} |")
    path.write_text('\n'.join(L), encoding='utf-8')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--run-date', default=datetime.now(CST).date().isoformat())
    ap.add_argument('--self-test', action='store_true', help='合成 smoke，不连库')
    args = ap.parse_args()
    if args.self_test:
        self_test()
        return
    run_date = args.run_date

    dsn = rising_streak.DSN.read_text().strip()
    from finance_p0a1.baostock_current_reader import resolve_current_baostock_scope
    # 费用模型最先加载：配置缺失/非法直接 fail-closed，不进入回填主流程
    fee = load_fee_model()
    with psycopg.connect(dsn, connect_timeout=5) as conn:
        scope = resolve_current_baostock_scope(conn, dataset_id=rising_streak.DATASET)
        latest = str(scope['latest_trade_date'])

        # 1. 信号收集（新口径：P0.8 产物对为唯一权威源，缺失不回退；
        #    legacy：回执 > 库 > md）
        p0a8_signals, signal_defects = collect_from_p0a8()
        signals = dict(p0a8_signals)  # signal_date -> {report_date, candidates, source}
        receipt_signals = collect_from_receipts()
        for asof, s in receipt_signals.items():
            if asof > LEGACY_CUTOFF:
                if asof not in signals:
                    signal_defects.append(f'信号日 {asof} 属新口径但无 P0.8 产物对，不回退旧回执')
                continue
            signals.setdefault(asof, s)
        db_sig = collect_from_db(conn)
        if db_sig:
            sd = prev_trading_day(date.fromisoformat(db_sig['report_date'])).isoformat()
            signals.setdefault(sd, {**db_sig, 'signal_inferred': True})
        md_signals = collect_from_md(covered_dates=set())
        for report_date, s in md_signals.items():
            if s.get('parse_status') != 'ok':
                continue
            sd = s['signal_date']
            if sd > LEGACY_CUTOFF:
                # 新口径序列唯一权威源是 P0.8 产物对，md 解析不回退
                if sd not in signals:
                    signal_defects.append(
                        f'信号日 {sd} 属新口径，md 解析仅作存在性提示，不作信号源')
                continue
            if sd in signals:
                continue  # 回执/库优先
            signals[sd] = {'report_date': report_date, 'candidates': s['candidates'],
                           'source': s['source']}
        md_parse_failures = {d: s['parse_status'] for d, s in md_signals.items()
                             if s.get('parse_status') != 'ok'}

        # 2. P1.1 对照分组
        cmp_map = {}
        cmp_files = sorted((WORKSPACE / 'finance_p0a1_artifacts/p1a1-compare').glob(
            'asof-*/compare_receipt.json'))
        for cf in cmp_files:
            cr = json.loads(cf.read_text(encoding='utf-8'))
            if not cr.get('ok'):
                continue
            asof = cr['asof']
            m = cmp_map.setdefault(asof, {})
            for o in cr['result']['overlap']:
                m[o['code']] = 'overlap'
            for d in cr['result']['local_only']:
                m.setdefault(d['code'], 'local_only')
            for d in cr['result']['external_only']:
                m.setdefault(d['code'], 'external_only')

        # 3. 回填
        all_codes = {c['code'] for s in signals.values() for c in s['candidates']}
        date_min = min(signals) if signals else latest
        bars = load_bars(conn, scope, all_codes, date_min, latest)
        index = load_index(conn)

    results = []
    for sd in sorted(signals):
        s = signals[sd]
        hd = {h: d for h, d in zip(HORIZONS, next_trading_days(sd, max(HORIZONS)))}
        for c in s['candidates']:
            r = backfill_candidate(c, sd, hd, bars, index, latest, fee)
            r['source'] = s['source']
            r['report_date'] = s['report_date']
            r['rank'] = c.get('rank')  # P0.8 Top10 名次（池内非 Top10 为 None）
            r['bucket'] = bucket_of(c['days'])
            r['cmp_group'] = cmp_map.get(sd, {}).get(c['code'], 'no_comparison')
            r['news_group'] = ('unavailable_legacy' if sd <= LEGACY_CUTOFF
                               else 'node1_p0a8' if s['source'] == 'p0a8_decision'
                               else 'pending_node1')
            r['caliber'] = 'legacy' if sd <= LEGACY_CUTOFF else 'new'
            results.append(r)

    # 3b. 四档组合账户（仅新口径 p0a8_decision 信号日；未到期 pending）
    portfolio_tiers = build_portfolio_tiers(signals, bars, index, latest, fee)

    # 4. 统计（legacy / new 隔离，永不合井）
    calibers = {}
    for calib in ('new', 'legacy'):
        sub = [r for r in results if r['caliber'] == calib]
        if sub:
            calibers[calib] = compute_block(sub)

    # 5. 样本纪律与暂存建议（仅对 new 口径；history 仅含 new 口径记录）
    history = load_bucket_history()
    suggestions, history_entry = [], None
    if calibers.get('new'):
        suggestions, history_entry = build_suggestions(run_date, calibers['new'], history)

    receipt = {'run_date': run_date, 'generated_at': datetime.now(CST).isoformat(),
               'caliber_version': 'v2',
               'latest_trade_date': latest, 'dataset_versions': scope['dataset_versions'],
               'reader_receipt_hash': scope['reader_receipt_hash'],
               'index': {'id': INDEX_ID, 'source': INDEX_SOURCE, 'rows': len(index)},
               'fee_model': {'rule_version': fee['rule_version'],
                             'path': str(DEFAULT_FEE_MODEL_PATH),
                             'budget_per_stock': fee['per_stock_budget'],
                             'commission_rate': fee['commission_rate'],
                             'commission_min': fee['commission_min'],
                             'stamp_rate_sell': fee['stamp_rate_sell'],
                             'transfer_rate': fee['transfer_rate']},
               'signal_dates': {sd: {'report_date': s['report_date'],
                                     'source': s['source'], 'count': len(s['candidates'])}
                                for sd, s in sorted(signals.items())},
               'signal_defects': signal_defects,
               'portfolio_tiers': portfolio_tiers,
               'md_parse_failures': md_parse_failures,
               'candidate_count': len(results),
               'calibers': calibers, 'suggestions': suggestions,
               'results': results}
    receipt['receipt_hash'] = 'sha256:' + hashlib.sha256(
        json.dumps(results, ensure_ascii=False, sort_keys=True,
                   separators=(',', ':')).encode()).hexdigest()

    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    rj = ARTIFACTS / f'backfill_{run_date}.json'
    rj.write_text(json.dumps(receipt, ensure_ascii=False, indent=1), encoding='utf-8')
    if history_entry:
        with (ARTIFACTS / 'bucket_history.jsonl').open('a', encoding='utf-8') as f:
            f.write(json.dumps(history_entry, ensure_ascii=False) + '\n')
    md_path = REPORTS_POSTMARKET / f'backfill_{run_date}.md'
    write_report(receipt, md_path)
    mirror = MIRROR_ROOT / 'postmarket' / md_path.name
    mirror.parent.mkdir(parents=True, exist_ok=True)
    mirror.write_text(md_path.read_text(encoding='utf-8'), encoding='utf-8')
    new_stats = calibers.get('new', {}).get('stats', {})
    print(json.dumps({'receipt': str(rj), 'report': str(md_path),
                      'mirror': str(mirror), 'hash': receipt['receipt_hash'][:24],
                      'candidates': len(results),
                      'legacy': len([r for r in results if r['caliber'] == 'legacy']),
                      'new': len([r for r in results if r['caliber'] == 'new']),
                      'new_T+2': new_stats.get('T+2')},
                     ensure_ascii=False, indent=1))


# ---------- 合成 smoke（python3 p1a3_backfill.py --self-test，不连库） ----------

def self_test():
    """合成验证：①P0.8 产物对配对与缺口 defect；②四档总额校验/末位放弃/闲置现金
    与费后净收益的手算基准一致。

    手算基准（fee_model 费率：佣金万5最低5元双边、印花税0.05%卖出、过户费0.001%双边）：
    单只 open=100 买 100 股：amount 10000，佣金 max(5,5)=5，过户 0.10
    → buy_cost=10005.10；B+1 收盘 110 卖出：proceeds 11000，佣金 5.5、印花税 5.5、
    过户 0.11 → net 10988.89 → 单只 pnl +983.79。
    3 万档：AAA+BBB 各 10005.10 = 20010.20 ≤ 30000；加 CCC 后 30015.30 > 30000
    → CCC 末位放弃；invested 20010.20，idle 9989.80；T+2 pnl = 2×983.79 = 1967.58。
    """
    import tempfile
    tmp = Path(tempfile.mkdtemp())
    fails = []

    def check(name, cond, detail=''):
        print(('  [OK] ' if cond else '  [FAIL] ') + name + (f' —— {detail}' if detail else ''))
        if not cond:
            fails.append(name)

    fee = load_fee_model()

    # ---- ① 配对：signal 2026-10-14 一对齐全；2026-10-16 只有 decision → defect ----
    pre_dir, dec_dir = tmp / 'evidence', tmp / 'decision'
    pre_dir.mkdir(); dec_dir.mkdir()
    cands = [{'code': c, 'name': f'票{c}', 'days': 4, 'script_score': 70}
             for c in ('600001', '600002', '600003', '600004')]
    (pre_dir / 'score_prescreen_2026-10-15.json').write_text(json.dumps({
        'run_date': '2026-10-15', 'signal_date': '2026-10-14',
        'candidates': cands}, ensure_ascii=False), encoding='utf-8')
    (dec_dir / 'decision_2026-10-15.json').write_text(json.dumps({
        'trade_date': '2026-10-15', 'data_date': '2026-10-14',
        'candidates': [{'rank': i + 1, 'code': c, 'name': f'票{c}',
                        'select_price': 10.0} for i, c in enumerate(
                            ('600001', '600002', '600003', '600004'))]},
        ensure_ascii=False), encoding='utf-8')
    (dec_dir / 'decision_2026-10-17.json').write_text(json.dumps({
        'trade_date': '2026-10-17', 'data_date': '2026-10-16', 'candidates': []},
        ensure_ascii=False), encoding='utf-8')

    global PRESCREEN_DIR, DECISION_DIR
    old_pre, old_dec = PRESCREEN_DIR, DECISION_DIR
    PRESCREEN_DIR, DECISION_DIR = pre_dir, dec_dir
    try:
        sigs, defects = collect_from_p0a8()
    finally:
        PRESCREEN_DIR, DECISION_DIR = old_pre, old_dec
    check('配对成功 2026-10-14', '2026-10-14' in sigs)
    check('Top10 rank 标记', sigs['2026-10-14']['candidates'][0]['rank'] == 1
          and sigs['2026-10-14']['candidates'][0]['in_top10'])
    check('缺 prescreen 的 10-16 报 defect',
          any('2026-10-16' in d for d in defects), str(defects))

    # ---- ② 四档组合：AAA/BBB/CCC 各 open=100 触发末位放弃；DDD 涨停不可成交 ----
    sd = '2026-10-14'
    hd = {h: d for h, d in zip(HORIZONS, next_trading_days(sd, max(HORIZONS)))}
    b_date, t2_date = hd[1], hd[2]

    def bar(o, c, preclose, lo=None):
        return {'open': o, 'close': c, 'preclose': preclose,
                'low': lo if lo is not None else min(o, c), 'trade_status': 1, 'is_st': False}

    bars = {}
    for code, o, c2 in (('600001.SH', 100.0, 110.0), ('600002.SH', 100.0, 110.0),
                        ('600003.SH', 100.0, 110.0)):
        bars[code] = {sd: bar(99, 100.0, 99), b_date: bar(o, 105.0, 100.0),
                      t2_date: bar(105.0, c2, 105.0)}
    # DDD：买入日开盘=涨停价（preclose 10 ×1.10）→ unbuyable
    bars['600004.SH'] = {sd: bar(9.9, 10.0, 9.9), b_date: bar(11.0, 11.0, 10.0),
                         t2_date: bar(11.0, 11.5, 11.0)}
    index = {sd: 4000.0, t2_date: 4040.0}
    latest = t2_date
    signals = {sd: {'source': 'p0a8_decision', 'report_date': '2026-10-15',
                    'candidates': [
                        {'code': '600001.SH', 'name': 'AAA', 'days': 4, 'in_top10': True, 'rank': 1},
                        {'code': '600002.SH', 'name': 'BBB', 'days': 4, 'in_top10': True, 'rank': 2},
                        {'code': '600003.SH', 'name': 'CCC', 'days': 4, 'in_top10': True, 'rank': 3},
                        {'code': '600004.SH', 'name': 'DDD', 'days': 4, 'in_top10': True, 'rank': 4},
                    ]}}
    pf = build_portfolio_tiers(signals, bars, index, latest, fee)
    t3w = pf[sd]['tiers'][0]  # 3 万档
    check('3万档成交 2 只（CCC 末位放弃）', t3w['accepted'] == 2,
          f"accepted={t3w['accepted']}")
    check('skipped_funds 为 rank3 CCC',
          len(t3w['skipped_funds']) == 1 and t3w['skipped_funds'][0]['code'] == '600003.SH')
    check('DDD 涨停不可成交不占预算',
          any(p['code'] == '600004.SH' and p['status'] == 'unbuyable_limit_up'
              for p in t3w['positions']))
    check('总投入手算一致 20010.20', abs(t3w['total_invested'] - 20010.20) < 0.05,
          f"{t3w['total_invested']}")
    check('闲置现金手算一致 9989.80', abs(t3w['idle_cash'] - 9989.80) < 0.05,
          f"{t3w['idle_cash']}")
    w2 = t3w['windows'].get('T+2', {})
    check('T+2 费后净收益手算一致 1967.58', abs(w2.get('net_pnl', 0) - 1967.58) < 0.1,
          f"{w2.get('net_pnl')}")
    check('T+2 收益率(档位) ≈ 6.559%',
          abs(w2.get('return_on_tier_pct', 0) - 6.5586) < 0.01,
          f"{w2.get('return_on_tier_pct')}")
    check('T+2 超额 pp = 收益率 − 指数 1.0%',
          abs(w2.get('alpha_on_tier_pp', 0) - (1967.58 / 30000 * 100 - 1.0)) < 0.02,
          f"{w2.get('alpha_on_tier_pp')}")
    check('15万档三只全成交（45015.30<150000 不触发放弃）',
          pf[sd]['tiers'][3]['accepted'] == 3)
    check('T+3 未到期 pending', pf[sd]['tiers'][0]['windows']['T+3']['status'] == 'pending')

    print(f'\n{"全部通过" if not fails else "失败: " + str(fails)}')
    sys.exit(1 if fails else 0)


if __name__ == '__main__':
    main()
