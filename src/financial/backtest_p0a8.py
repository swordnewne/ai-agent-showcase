#!/usr/bin/env python3
"""P0.8 回测式补跑驱动（2026-10-13）：用归档新闻 PIT 回放 10-08~10-12 五日的
节点1 → 预筛 → 逐只评审 → 闸门 → decision 全链路。

纪律（账本 v1.18）：
- 产物一律打 caliber=backtest 水印，单独目录 reports/_backtest_20261013/ 存放，
  不混入 10-14 起的 live 序列，不进生产 evidence/decision_packets 目录；
- 新闻窗口严格 PIT：cutoff 固定为报告日 08:05，打桩禁止实采（只读归档）；
- 行情候选严格 PIT：fetch_candidates(report_date) 经 current reader 读历史收盘；
- 偏差声明：隔夜海外市场/基金净值无 PIT 数据源，节点1 prompt 的对应段落以
  「回测无 PIT 数据」占位——news_screening（P0.8 唯一消费的节点1输出）不受影响，
  但 market_summary/fund_161130/a_share_scenarios 字段不具回测意义，不用于评估。

用法：python3 backtest_p0a8.py [2026-10-08 2026-10-09 ...]
"""
import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import psycopg  # noqa: E402
import preopen_report as pr  # noqa: E402
import rising_streak  # noqa: E402
import score_prescreen  # noqa: E402
import p0a8_review  # noqa: E402

CST = timezone(timedelta(hours=8))
BACKTEST_DIR = Path('/root/.openclaw/workspace/reports/_backtest_20261013')
DATES_DEFAULT = ['2026-10-08', '2026-10-09', '2026-10-10', '2026-10-11', '2026-10-12']

# 打桩：禁止真实采集（实采会追加写归档，污染证据链）
pr._collect_news_snapshot = lambda *a, **k: None


def backtest_node1(report_date, news_list, window, out_dir):
    """回测版节点1：prompt 行情段占位（无 PIT 源），news_screening 语义不变。"""
    news_summary = [pr._format_news_ref_line(i, n) for i, n in enumerate(news_list, 1)]
    prompt = f"""你是一位盘前资讯分析师。基于以下数据，生成结构化的盘前分析报告。

【回测声明】隔夜海外市场与 161130 基金段落无 PIT 数据，一律填 "backtest_unavailable"，
不要编造；a_share_scenarios 与 news_screening 基于下方新闻正常生成。

## 国内新闻（窗口 {window.get('start_at')} 至 {window.get('cutoff_at')}，PIT 归档回放）
{chr(10).join(news_summary)}

## 输出要求（严格JSON格式）
字段同生产 schema：market_summary / fund_161130 / a_share_scenarios /
news_screening{{selected[ref,column,event_type,direction,impact,evidence_strength],
coverage[column,status,reason], dropped_summary}} / facts / missing_data / risk_notes。
规则：news_screening.selected 只列答得上板块影响的条目（精选非配额）；
ref 必须对应上文新闻列表编号；五栏目逐一声明状态，不许凑数。
"""
    ai_result = pr.call_deepseek(prompt)
    if 'error' in ai_result:
        return None, ai_result['error']
    news_refs = []
    for i, n in enumerate(news_list, 1):
        line = pr._format_news_ref_line(i, n)
        news_refs.append({'ref': i, 'source': n.get('media', '未知来源'),
                          'title': n.get('title', ''),
                          'url': str(n.get('source_url') or n.get('url') or ''),
                          'content_hash': 'sha256:' + hashlib.sha256(
                              line.encode('utf-8')).hexdigest()})
    _, _, model_name, _, effort = pr._load_deepseek_config()
    snapshot = {'trade_date': report_date, 'caliber': 'backtest',
                'generated_at': datetime.now(CST).isoformat(),
                'model': model_name, 'reasoning_effort': effort,
                'input_hash': 'sha256:' + hashlib.sha256(prompt.encode('utf-8')).hexdigest(),
                'news_count': len(news_list),
                'news_window_counts': window.get('news_window_counts'),
                'news_refs': news_refs, 'result': ai_result}
    snap_path = out_dir / f'node1_{report_date}.json'
    snap_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=1), encoding='utf-8')
    return snapshot, None


def backtest_prescreen(report_date, streak_result, out_dir):
    """回测版预筛：复用 score_candidate 逐只算，PIT 由 fetch_candidates 保证。"""
    scope = {'dataset_versions': streak_result['dataset_versions']}
    dsn = rising_streak.DSN.read_text().strip()
    signal_date = streak_result['date']
    with psycopg.connect(dsn, connect_timeout=5) as conn:
        bar_map = score_prescreen.load_bars(
            conn, scope,
            [score_prescreen.with_suffix(c['code']) for c in streak_result['verified_candidates']],
            signal_date)
    scored, unresolved = [], []
    for c in streak_result['verified_candidates']:
        entry, reason = score_prescreen.score_candidate(
            c, bar_map.get(score_prescreen.with_suffix(c['code']), []), signal_date)
        if entry:
            scored.append(entry)
        else:
            unresolved.append({'code': c['code'], 'name': c.get('name'), 'reason': reason})
    scored.sort(key=lambda e: -e['script_score'])
    payload = {'run_date': report_date, 'signal_date': signal_date,
               'caliber': 'backtest', 'rule_version': score_prescreen.RULE_VERSION,
               'dataset_versions': streak_result['dataset_versions'],
               'generated_at': datetime.now(CST).isoformat(),
               'candidate_count': len(streak_result['verified_candidates']),
               'scored_count': len(scored), 'candidates': scored,
               'unresolved': unresolved}
    path = out_dir / f'score_prescreen_{report_date}.json'
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding='utf-8')
    return payload


def run_one(report_date, out_dir):
    print(f'\n===== 回测 {report_date} =====')
    cutoff = datetime.fromisoformat(report_date).replace(hour=8, minute=5, tzinfo=CST)
    news_list, window = pr.fetch_news_window(report_date, cutoff_at=cutoff)
    print(f'新闻窗口回放: {len(news_list)} 条（{window.get("collection_status")}）')
    if not news_list:
        return {'date': report_date, 'ok': False, 'reason': '回放窗口为空'}

    snapshot, err = backtest_node1(report_date, news_list, window, out_dir)
    if err:
        return {'date': report_date, 'ok': False, 'reason': f'节点1失败: {err}'}
    selected = ((snapshot['result'].get('news_screening') or {}).get('selected') or [])
    print(f'节点1: 精选 {len(selected)} 条')

    streak = pr.fetch_candidates(report_date)
    if not streak.get('ok'):
        return {'date': report_date, 'ok': False, 'reason': f"候选失败: {streak.get('reason')}"}
    print(f"候选: {streak.get('verified')}/{streak.get('total')} 只（信号日 {streak['date']}）")

    pre = backtest_prescreen(report_date, streak, out_dir)
    elig = sum(1 for e in pre['candidates'] if e['script_score'] >= p0a8_review.PRESCREEN_THRESHOLD)
    print(f"预筛: {pre['scored_count']} 只，过线(≥55) {elig} 只")

    out = p0a8_review.run_p0a8(report_date, streak,
                               evidence_dir=out_dir, decision_dir=out_dir,
                               call_llm=pr.call_deepseek)
    if not out.get('ok'):
        return {'date': report_date, 'ok': False, 'reason': f"P0.8: {out.get('reason')}"}
    # 打 backtest 水印（decision JSON 由 run_p0a8 原子写入，此处补 caliber 标记）
    dpath = Path(out['decision_path'])
    d = json.loads(dpath.read_text(encoding='utf-8'))
    d['caliber'] = 'backtest'
    dpath.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding='utf-8')
    print(f"P0.8: 评审 {d['review']['called']} 过线 {d['qualified_count']} "
          f"推荐 {len(d['candidates'])} 只 → {dpath.name}")
    return {'date': report_date, 'ok': True, 'qualified': d['qualified_count'],
            'recommended': [c['code'] for c in d['candidates']]}


def main():
    dates = sys.argv[1:] or DATES_DEFAULT
    out_dir = BACKTEST_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = []
    for d in dates:
        try:
            summary.append(run_one(d, out_dir))
        except Exception as exc:
            summary.append({'date': d, 'ok': False, 'reason': f'未捕获异常: {exc}'})
    (out_dir / 'backtest_summary.json').write_text(
        json.dumps({'generated_at': datetime.now(CST).isoformat(),
                    'caliber': 'backtest', 'runs': summary},
                   ensure_ascii=False, indent=1), encoding='utf-8')
    print('\n===== 汇总 =====')
    for s in summary:
        print(s)
    print(f'产物目录: {out_dir}（caliber=backtest，不混入 live 序列）')


if __name__ == '__main__':
    main()
