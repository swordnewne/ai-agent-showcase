#!/usr/bin/env python3
"""P1.1 对照：Kimi Work iFinD(同花顺)工作流候选清单 vs 本地 BaoStock 主板连涨筛选。

原则（账本 P1.1）：
- 本地筛选走 rising_streak.fetch_candidates 独立规则，不读取外部结果；
- 外部原始响应（报告 md / px_*.csv / info_*.csv / shares.json / indicators.json）
  快照复制进 artifacts 并记 sha256；
- 输出重合、外部独有、本地独有与差异原因；无法解释的差异标 comparison_unresolved。

用法：
  python3 p1a1_streak_compare.py --asof 2026-10-12 --report-date 2026-10-13 \
      --external-dir '/mnt/c/Users/smk/Desktop/kimi-a股分析/_work/2026-10-12'
"""
import argparse
import csv
import hashlib
import json
import re
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import psycopg  # noqa: E402
import rising_streak  # noqa: E402

CST = timezone(timedelta(hours=8))
MAINBOARD_SUFFIX = {'6': '.SH', '0': '.SZ', '9': '.SH'}


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def with_suffix(code6: str) -> str:
    return code6 + MAINBOARD_SUFFIX.get(code6[0], '.SH')


def snapshot_external(external_dir: Path, out_dir: Path) -> dict:
    """复制外部原始响应到 artifacts，返回清单。"""
    snap = out_dir / 'external'
    snap.mkdir(parents=True, exist_ok=True)
    patterns = ['preopen*.md', 'indicators.json', 'shares.json', 'px_*.csv', 'info_*.csv']
    manifest = {}
    for pat in patterns:
        for src in sorted(external_dir.glob(pat)):
            dst = snap / src.name
            shutil.copy2(src, dst)
            manifest[src.name] = {
                'sha256': sha256_file(dst),
                'bytes': dst.stat().st_size,
                'src': str(src),
            }
    if not any(k.startswith('preopen') for k in manifest):
        raise FileNotFoundError(f'外部目录未找到 preopen 报告：{external_dir}')
    (snap / 'manifest.json').write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    return manifest


def parse_external_report(md_path: Path) -> dict:
    """从报告 md 解析候选：评分汇总表 top10 + 未入选说明。"""
    text = md_path.read_text(encoding='utf-8')
    candidates = {}
    for m in re.finditer(
            r'^\|\s*(\d+)\s*\|\s*([^|]+?)\s*\|\s*(\d{6})\.(SH|SZ)\s*\|[^|]*\|\s*(\d+)\s*天\s*\|[^\n]*?\*\*(\d+)\*\*\s*\|',
            text, re.M):
        rank, name, code6, suffix, days, total = m.groups()
        candidates[f'{code6}.{suffix}'] = {
            'name': name.strip(), 'rank': int(rank), 'days': int(days),
            'total_score': int(total), 'in_top10': True, 'source': 'md_table',
        }
    mtail = re.search(r'###\s*未入选说明(.*?)(?:\n##|\Z)', text, re.S)
    unparsed_tail = None
    if mtail:
        tail = mtail.group(1)
        for m in re.finditer(r'([0-9A-Za-z一-鿿]+)（(\d{6})，(\d+)(?:\s*分)?', tail):
            name, code6, score = m.groups()
            code = with_suffix(code6)
            if code not in candidates:
                candidates[code] = {
                    'name': name, 'rank': None, 'days': None,
                    'total_score': int(score), 'in_top10': False,
                    'source': 'md_unselected',
                }
        # 早间候选中断名单（无代码，仅记录）
        mbrk = re.search(r'早间候选(.+?)今日下跌', tail)
        if mbrk:
            unparsed_tail = [n for n in re.split(r'[、，,\s]+', mbrk.group(1).strip()) if n]
    return {'candidates': candidates, 'untracked_names': unparsed_tail,
            'md_sha256': sha256_text(text)}


def load_external_universe(external_dir: Path) -> dict:
    """外部实际拉取过的股票全集（px csv / indicators / shares）。"""
    pulled = set()
    for csv_path in sorted(external_dir.glob('px_*.csv')):
        with csv_path.open(encoding='utf-8', newline='') as f:
            for row in csv.DictReader(f):
                if row.get('thscode'):
                    pulled.add(row['thscode'].strip())
    extra = {}
    for name in ('indicators.json', 'shares.json'):
        p = external_dir / name
        if p.exists():
            data = json.loads(p.read_text(encoding='utf-8'))
            extra[name] = sorted(data.keys())
            pulled.update(data.keys())
    indicators = {}
    p = external_dir / 'indicators.json'
    if p.exists():
        indicators = json.loads(p.read_text(encoding='utf-8'))
    return {'pulled': sorted(pulled), 'by_file': extra, 'indicators': indicators}


def external_bars_from_px(external_dir: Path) -> dict:
    """从 px_*.csv 重建外部日线（raw close），用于逐日符号对照。"""
    bars = {}
    for csv_path in sorted(external_dir.glob('px_*.csv')):
        with csv_path.open(encoding='utf-8', newline='') as f:
            for row in csv.DictReader(f):
                code = (row.get('thscode') or '').strip()
                t = (row.get('time') or '').strip()
                if not code or not t:
                    continue
                try:
                    close = float(row['close'])
                except (KeyError, TypeError, ValueError):
                    continue
                day = f'{t[:4]}-{t[4:6]}-{t[6:8]}'
                bars.setdefault(code, {})[day] = close
    return {code: sorted(d.items()) for code, d in bars.items()}


def streak_from_closes(day_close: list) -> dict:
    """(day, close) 升序 -> 末日连涨天数与逐日涨跌符号。"""
    pcts = []
    for i in range(1, len(day_close)):
        prev, cur = day_close[i - 1][1], day_close[i][1]
        if prev and prev > 0:
            pcts.append((day_close[i][0], (cur / prev - 1) * 100))
    days = 0
    for _, pct in reversed(pcts):
        if pct > 0:
            days += 1
        else:
            break
    return {'streak': days, 'tail_pcts': pcts[-12:]}


def query_local_bars(conn, scope, security_id: str, asof: str) -> list:
    with conn.cursor() as cur:
        cur.execute(
            """SELECT trade_date, close, preclose, pct_chg, trade_status, is_st
                 FROM finance.daily_bars
                WHERE dataset_id=%s AND dataset_version=ANY(%s) AND adjust_type='raw'
                  AND source_id=%s AND security_id=%s
                  AND trade_date BETWEEN %s AND %s
                ORDER BY trade_date""",
            (rising_streak.DATASET, scope['dataset_versions'], rising_streak.SOURCE,
             security_id,
             datetime.fromisoformat(asof).date() - timedelta(days=60), asof))
        rows = cur.fetchall()
    return [{'trade_date': str(r[0]), 'close': float(r[1]) if r[1] is not None else None,
             'pct_chg': float(r[3]) if r[3] is not None else None,
             'trade_status': r[4], 'is_st': r[5]} for r in rows]


def local_streak_of(bars: list) -> int:
    days = 0
    for b in reversed(bars):
        if b['trade_status'] == 1 and b['pct_chg'] is not None and b['pct_chg'] > 0:
            days += 1
        else:
            break
    return days


def compare_day_signs(local_bars: list, ext_day_close: list, asof: str, n: int = 10):
    """逐日涨跌符号对照，返回不一致日列表。"""
    ext_pct = {}
    for i in range(1, len(ext_day_close)):
        prev, cur = ext_day_close[i - 1][1], ext_day_close[i][1]
        if prev and prev > 0:
            ext_pct[ext_day_close[i][0]] = (cur / prev - 1) * 100
    diffs = []
    local_tail = [b for b in local_bars if b['trade_date'] <= asof][-n:]
    for b in local_tail:
        d = b['trade_date']
        if d in ext_pct and b['pct_chg'] is not None:
            if (b['pct_chg'] > 0) != (ext_pct[d] > 0):
                diffs.append({'date': d, 'local_pct': round(b['pct_chg'], 3),
                              'external_pct': round(ext_pct[d], 3)})
    return diffs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--asof', required=True, help='对照基准收盘日，如 2026-10-12')
    ap.add_argument('--report-date', required=True, help='盘前报告日，如 2026-10-13')
    ap.add_argument('--external-dir', required=True)
    ap.add_argument('--out-root',
                    default='/root/.openclaw/workspace/finance_p0a1_artifacts/p1a1-compare')
    args = ap.parse_args()

    external_dir = Path(args.external_dir)
    out_dir = Path(args.out_root) / f'asof-{args.asof}'
    out_dir.mkdir(parents=True, exist_ok=True)

    receipt = {'asof': args.asof, 'report_date': args.report_date,
               'generated_at': datetime.now(CST).isoformat(),
               'external_dir': str(external_dir)}

    # 1. 外部原始响应快照 + 哈希
    receipt['external_manifest'] = snapshot_external(external_dir, out_dir)

    # 2. 外部解析
    md_files = [k for k in receipt['external_manifest'] if k.startswith('preopen')]
    md_path = external_dir / sorted(md_files)[-1]
    parsed = parse_external_report(md_path)
    universe = load_external_universe(external_dir)
    ext_px = external_bars_from_px(external_dir)
    receipt['external'] = {
        'report_md': md_path.name,
        'candidates': parsed['candidates'],
        'untracked_names': parsed['untracked_names'],
        'pulled_universe_size': len(universe['pulled']),
        'pulled_universe': universe['pulled'],
    }
    (out_dir / 'external_parse.json').write_text(
        json.dumps(receipt['external'], ensure_ascii=False, indent=2), encoding='utf-8')

    # 3. 本地独立筛选（不读外部结果）
    local = rising_streak.fetch_candidates(args.report_date, expected_date=args.asof)
    receipt['local_receipt_path'] = local.get('receipt_path')
    receipt['local_receipt_hash'] = local.get('receipt_hash')
    if not local.get('ok'):
        receipt['ok'] = False
        receipt['reason'] = f"本地筛选失败：{local.get('reason')}"
        _finish(receipt, out_dir)
        return
    local_verified = {with_suffix(s['code']): s for s in local['verified_candidates']}
    local_excluded = {e['security_id']: e['reason'] for e in local['excluded']}
    receipt['local'] = {
        'dataset_versions': local['dataset_versions'],
        'data_quality_status': local['data_quality_status'],
        'total_prescreen': local['total'],
        'verified': len(local_verified),
        'excluded': local_excluded,
    }

    ext_candidates = parsed['candidates']
    pulled = set(universe['pulled'])
    indicators = universe['indicators']

    # 4. 逐候选对照
    overlap, diffs, unresolved = [], [], []
    dsn = rising_streak.DSN.read_text().strip()
    with psycopg.connect(dsn, connect_timeout=5) as conn:
        scope = {'dataset_versions': local['dataset_versions']}

        for code, ext in sorted(ext_candidates.items()):
            if code in local_verified:
                lv = local_verified[code]
                entry = {'code': code, 'name': ext['name'],
                         'external_days': ext['days'], 'local_days': lv['days'],
                         'external_score': ext['total_score'],
                         'local_score': lv['score'], 'in_top10': ext['in_top10']}
                if ext['days'] is not None and ext['days'] != lv['days']:
                    entry['days_mismatch'] = True
                    entry['day_sign_diffs'] = compare_day_signs(
                        query_local_bars(conn, scope, code, args.asof),
                        ext_px.get(code, []), args.asof)
                    unresolved.append({**entry, 'issue': 'streak_days_mismatch'})
                overlap.append(entry)
                continue
            if code in local_excluded:
                # 本地初选命中但深度校验被护栏拒绝（如窗口内停牌字段缺失）——规则差异，可解释
                entry = {'code': code, 'name': ext['name'], 'side': 'external_only',
                         'classification': 'explained_local_guard',
                         'reason': f"本地护栏排除：{local_excluded[code]}",
                         'external_days': ext['days'], 'external_score': ext['total_score'],
                         'in_top10': ext['in_top10']}
                diffs.append(entry)
                continue
            # 外部有、本地初选没有 -> 查本地库找原因
            reason = classify_external_only(conn, scope, code, ext, args.asof,
                                            ext_px.get(code))
            entry = {'code': code, 'name': ext['name'], **reason}
            diffs.append(entry)
            if reason['classification'] == 'comparison_unresolved':
                unresolved.append(entry)

        for code, lv in sorted(local_verified.items()):
            if code in ext_candidates:
                continue
            reason = classify_local_only(conn, scope, code, lv, pulled, indicators,
                                         ext_px.get(code), args.asof)
            entry = {'code': code, 'name': lv['name'], 'local_days': lv['days'],
                     'local_score': lv['score'], **reason}
            diffs.append(entry)
            if reason['classification'] == 'comparison_unresolved':
                unresolved.append(entry)

    # 5. 外部"早间候选中断"名单（无代码）核验：本地库基准日是否确实下跌
    with psycopg.connect(dsn, connect_timeout=5) as conn2:
        untracked_check = verify_untracked_names(
            conn2, scope, parsed['untracked_names'], args.asof)
    receipt['untracked_morning_candidates_check'] = untracked_check

    receipt['result'] = {
        'overlap_count': len(overlap),
        'overlap': overlap,
        'external_only': [d for d in diffs if d.get('side') == 'external_only'],
        'local_only': [d for d in diffs if d.get('side') == 'local_only'],
        'comparison_unresolved': unresolved,
    }
    receipt['ok'] = True
    _finish(receipt, out_dir)


def classify_external_only(conn, scope, code, ext, asof, ext_day_close):
    """外部候选在本地初选缺失的原因分类。"""
    bars = query_local_bars(conn, scope, code, asof)
    base = {'side': 'external_only', 'external_days': ext['days'],
            'external_score': ext['total_score'], 'in_top10': ext['in_top10']}
    if not bars:
        return {**base, 'classification': 'comparison_unresolved',
                'reason': '本地库无该证券日线记录'}
    upto = [b for b in bars if b['trade_date'] <= asof]
    if not upto or upto[-1]['trade_date'] != asof:
        return {**base, 'classification': 'comparison_unresolved',
                'reason': f"本地库基准日{asof}无记录（最新 {upto[-1]['trade_date'] if upto else '无'}）"}
    if upto[-1]['is_st']:
        return {**base, 'classification': 'explained',
                'reason': '本地标记 ST，按规则排除'}
    lstreak = local_streak_of(upto)
    base['local_days'] = lstreak
    if lstreak >= 3:
        return {**base, 'classification': 'comparison_unresolved',
                'reason': f'本地连涨{lstreak}天却未进初选，需排查初选SQL'}
    detail = f'本地连涨{lstreak}天（<3），不满足筛选'
    if ext_day_close:
        diffs = compare_day_signs(upto, ext_day_close, asof)
        if diffs:
            base['day_sign_diffs'] = diffs
            if len(diffs) == 1 and abs(diffs[0]['local_pct']) < 3:
                return {**base, 'classification': 'explained_possible_adjustment',
                        'reason': detail + f"；{diffs[0]['date']} 涨跌符号不一致"
                                f"（本地 {diffs[0]['local_pct']}% / 外部 {diffs[0]['external_pct']}%），"
                                '疑似复权口径或边界日差异'}
            return {**base, 'classification': 'comparison_unresolved',
                    'reason': detail + f'；{len(diffs)} 个交易日涨跌符号不一致'}
    return {**base, 'classification': 'explained_rule', 'reason': detail}


def classify_local_only(conn, scope, code, lv, pulled, indicators, ext_day_close,
                        asof):
    """本地候选在外部清单缺失的原因分类。"""
    base = {'side': 'local_only'}
    ext_ind = indicators.get(code)
    if code in pulled:
        # 外部拉取并计算过该证券，但报告既未入选也未列入落选说明
        if ext_ind:
            base['external_streak'] = ext_ind.get('streak')
            base['external_close'] = ext_ind.get('close')
        if ext_ind and ext_ind.get('streak') is not None and ext_ind['streak'] >= 3:
            diffs = compare_day_signs(query_local_bars(conn, scope, code, asof),
                                      ext_day_close or [], asof)
            if diffs:
                return {**base, 'classification': 'comparison_unresolved',
                        'reason': f"外部连涨{ext_ind['streak']}天/本地{lv['days']}天，"
                                  f'且逐日涨跌符号不一致：{diffs}'}
            return {**base, 'classification': 'explained_external_omission',
                    'reason': f"外部数据与本地一致（连涨{ext_ind['streak']}天），"
                    '但外部报告未披露该复核池成员的落选结果'}
        if ext_ind:
            diffs = compare_day_signs(query_local_bars(conn, scope, code, asof),
                                      ext_day_close or [], asof)
            return {**base, 'classification': 'comparison_unresolved',
                    'reason': f"外部判定连涨{ext_ind.get('streak')}天（<3 未入选），"
                              f"本地判定{lv['days']}天；符号不一致日：{diffs or '无（需查外部算法）'}"}
        return {**base, 'classification': 'comparison_unresolved',
                'reason': '外部已拉取该证券但无指标记录且未列入报告'}
    # 外部未拉取：复核池规模有限（当晚仅 33 只），快照内容无回执可查。
    # 连涨≥5天者按外部公开口径"应基本覆盖"，记为外部快照覆盖缺口发现，而非数据冲突。
    if lv['days'] >= 5:
        return {**base, 'classification': 'explained_snapshot_gap',
                'reason': f"本地连涨{lv['days']}天（≥5），外部复核池未覆盖；"
                '指向外部快照/复核池覆盖不足，不否定本地筛选'}
    return {**base, 'classification': 'explained_coverage',
            'reason': f"本地连涨{lv['days']}天（3~4天区间），属外部已声明的候选池覆盖缺口"
            '（快照截至前一交易日仅覆盖≥4天连涨）'}


def verify_untracked_names(conn, scope, names, asof):
    """外部早间候选（仅名称，声称当日下跌中断）在本地库的当日涨跌核验。"""
    if not names:
        return None
    out = []
    with conn.cursor() as cur:
        for raw in names:
            name = raw.rstrip('等').strip()
            if not name:
                continue
            cur.execute(
                """SELECT b.security_id, b.pct_chg FROM finance.daily_bars b
                   JOIN finance.securities s USING (security_id)
                   WHERE s.code_name=%s AND b.dataset_id=%s
                     AND b.dataset_version=ANY(%s) AND b.adjust_type='raw'
                     AND b.source_id=%s AND b.trade_date=%s""",
                (name, rising_streak.DATASET, scope['dataset_versions'],
                 rising_streak.SOURCE, asof))
            rows = cur.fetchall()
            if not rows:
                out.append({'name': name, 'status': 'not_found_in_local_db'})
                continue
            sid, pct = rows[0]
            agree = pct is not None and pct <= 0
            out.append({'name': name, 'security_id': sid,
                        'local_pct_chg': float(pct) if pct is not None else None,
                        'external_claim': '当日下跌连涨中断',
                        'agree': agree})
    return out


def _finish(receipt: dict, out_dir: Path):
    receipt['receipt_hash'] = 'sha256:' + hashlib.sha256(
        json.dumps(receipt, ensure_ascii=False, sort_keys=True,
                   separators=(',', ':')).encode()).hexdigest()
    rj = out_dir / 'compare_receipt.json'
    rj.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding='utf-8')
    _write_summary(receipt, out_dir / 'compare_summary.md')
    print(json.dumps({
        'ok': receipt.get('ok'),
        'overlap': receipt.get('result', {}).get('overlap_count'),
        'external_only': len(receipt.get('result', {}).get('external_only', [])),
        'local_only': len(receipt.get('result', {}).get('local_only', [])),
        'unresolved': len(receipt.get('result', {}).get('comparison_unresolved', [])),
        'receipt': str(rj), 'hash': receipt['receipt_hash'],
    }, ensure_ascii=False, indent=2))


def _write_summary(receipt: dict, path: Path):
    lines = [f"# P1.1 筛选对照（基准日 {receipt['asof']} 收盘）", '',
             f"- 生成时间：{receipt['generated_at']}",
             f"- 本地回执：`{receipt.get('local_receipt_path')}`",
             f"- 内容哈希：`{receipt['receipt_hash']}`", '']
    if not receipt.get('ok'):
        lines.append(f"本地筛选失败：{receipt.get('reason')}")
        path.write_text('\n'.join(lines), encoding='utf-8')
        return
    r = receipt['result']
    snap_gaps = [d for d in r['local_only'] if d['classification'] == 'explained_snapshot_gap']
    omissions = [d for d in r['local_only'] if d['classification'] == 'explained_external_omission']
    lines += [f"## 总览", '',
              f"| 重合 | 仅外部 | 仅本地 | 未解释差异 |",
              f"|---:|---:|---:|---:|",
              f"| {r['overlap_count']} | {len(r['external_only'])} | "
              f"{len(r['local_only'])} | {len(r['comparison_unresolved'])} |", '']
    lines.append('## 重合候选（连涨天数 外部/本地）')
    for o in r['overlap']:
        flag = f" ⚠️天数不一致 {o.get('day_sign_diffs') or ''}" if o.get('days_mismatch') else ''
        lines.append(f"- {o['name']}（{o['code']}）：{o['external_days']}/{o['local_days']} 天"
                     f"，外部 {o['external_score']} 分 / 本地 {o['local_score']} 分{flag}")
    for key, title in (('external_only', '仅外部候选'), ('local_only', '仅本地候选（3~4 天覆盖缺口从略，见 JSON）')):
        lines += ['', f'## {title}']
        for d in r[key]:
            if key == 'local_only' and d['classification'] in (
                    'explained_coverage', 'explained_snapshot_gap',
                    'explained_external_omission'):
                continue
            lines.append(f"- {d.get('name', '?')}（{d['code']}）：{d['reason']}"
                         f"〔{d['classification']}〕")
    if snap_gaps:
        lines += ['', f'## 外部快照覆盖缺口发现（本地连涨≥5天、外部复核池未覆盖，共 {len(snap_gaps)} 只）',
                  '指向外部工作流候选池完整性，非本地数据冲突。']
        for d in snap_gaps:
            lines.append(f"- {d.get('name', '?')}（{d['code']}）：本地连涨 {d['local_days']} 天")
    if omissions:
        lines += ['', '## 外部报告披露遗漏（数据一致但外部未披露落选结果）']
        for d in omissions:
            lines.append(f"- {d.get('name', '?')}（{d['code']}）：{d['reason']}")
    uc = receipt.get('untracked_morning_candidates_check')
    if uc:
        agree = sum(1 for x in uc if x.get('agree'))
        lines += ['', f'## 外部早间中断名单核验（{agree}/{len(uc)} 与本地一致）']
        for x in uc:
            lines.append(f"- {x['name']}：本地当日涨跌 {x.get('local_pct_chg')}"
                         f"{' ✅一致' if x.get('agree') else ' ❌不一致/未找到'}")
    if r['comparison_unresolved']:
        lines += ['', '## ⚠️ comparison_unresolved（不进入正常评分，需逐日追踪）']
        for u in r['comparison_unresolved']:
            lines.append(f"- {u.get('name', '?')}（{u['code']}）：{u.get('reason') or u.get('issue')}")
    path.write_text('\n'.join(lines), encoding='utf-8')


if __name__ == '__main__':
    main()
