#!/usr/bin/env python3
"""P0.8 主评分、风险闸门与 decision JSON（score_rules v1.0 / gate v1）。

链路位置：preopen_report.run() 在 fetch_candidates 之后调用 run_p0a8()。
本模块不负责出报告全文，只产出 decision_{trade_date}.json 与报告用榜单 markdown。

已定口径（2026-10-13 用户拍板）：
- 预筛阈值 55（= 合格线 65 − 模型最大加分 10 的数学推论；设计稿原推导 40=65−25
  有误，E消息面≤0 只扣分、F催化≤+10，模型可调上限实为 10；<55 的票数学上不可能
  合格，评审它们是纯浪费，零误杀）；
- 评审上限 Top40（按 script_score 降序截断，晨峰时间预算护栏，非配额）；
  被截断的记 guardrail_dropped，照常参与回填，误杀可由回填数据监测。

空头审查（对照组设计吸收）：
- verdict=holds → 剔除（bear_excluded），不进 Top10，由后续名次递补；
- verdict=partially_holds → 降级一档（total_adj = total − 5，记 bear_demotion）并触发复核；
  「一档」无官方定义，v1 定为 5 分，写入 decision JSON 供校准期回看；
- verdict=not_holds → 保留。

闸门三问（全部本地判定，回执分离）：
- Q1 证据完整性（硬检查，不过则整链降级 partial，不靠复核修好）；
- Q2 不确定性复核触发器：bear partially_holds ∨ 总分 ∈ [60,70] ∨ (E<0 且 F>0)；
  复核=独立一次性请求，只看评分卡+证据包、不看主评分 E/F/总分/bear，
  输出 recommend|not_recommend；一致性 v1 = 复核方向与「total_adj≥65」一致；
  不一致/无效 → 该候选 review_partial，移出推荐，报告明示待人工确认；
- Q3 机会门槛：total_adj ≥ 65（冻结值，未校准，全部标 uncalibrated）；
  Top10 是上限不是配额；全候选不过线 → 推荐列表为空并如实说明。

fail-closed 纪律：模型输出 schema 非法 / ref 越界 / 调用失败 → 该候选
review_failed，不进排序、不置零、不估算；原因逐只记录。

长期证据分档（P1.2 接线，2026-10-13 用户拍板口径）：评审池全员附 250 日
历史摘要（普通档）；Q2 复核触发者为重点候选，复核 prompt 附 750 日窗口
统计（重点档，不含逐日紧凑序列）；摘要完整性字段（缺失日/重复日/实际条数）
不进 prompt，进 decision JSON candidates[].data_quality，并入 Q1 证据链。
"""
import hashlib
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
try:
    import psycopg  # noqa: E402
    import rising_streak  # noqa: E402  复用 DSN
    import score_prescreen  # noqa: E402  复用 load_bars / score_candidate / with_suffix
    import history_digest  # noqa: E402  P1.2 长期摘要（普通 250 日/重点 750 日分档）
except ImportError:  # 非 WSL 环境（无 psycopg）仅可跑合成测试（_prescreen 被 stub）
    psycopg = None
    rising_streak = None
    score_prescreen = None
    history_digest = None

CST = timezone(timedelta(hours=8))
RULE_VERSION = 'score_rules-v1.0'
GATE_VERSION = 'gate-v1'
PRESCREEN_THRESHOLD = 55   # 2026-10-13 修正：65 − 模型最大加分 10
REVIEW_CAP = 40            # 晨峰时间预算护栏（非配额）
QUALIFY_LINE = 65
REVIEW_BAND = (60, 70)     # Q2 边界带触发器
BEAR_DEMOTION = -5         # bear partially_holds 降级一档的 v1 定义
MAX_WORKERS = 4            # 防触发限流的小并发
TOP_N = 10

E_VALUES = (0, -5, -10)    # 消息面合法取值


# ---------- 工具 ----------

def _sha256_text(text: str) -> str:
    return 'sha256:' + hashlib.sha256(text.encode('utf-8')).hexdigest()


def _sha256_file(path: Path) -> str:
    return 'sha256:' + hashlib.sha256(path.read_bytes()).hexdigest()


def _atomic_write_json(path: Path, payload: dict) -> None:
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding='utf-8')
    tmp.replace(path)


def _rating(total_adj: float) -> str:
    """评级标签 v1.0 约定（本地定义，非量表内容）：≥75 积极 / 65~75 谨慎 / <65 观察。"""
    if total_adj >= 75:
        return '积极'
    if total_adj >= QUALIFY_LINE:
        return '谨慎'
    return '观察'


# ---------- 输入装载与 Q1 硬检查 ----------

def _load_inputs(trade_date, streak_result, evidence_dir):
    """装载预筛输入；返回 (inputs, defects)。任何缺陷都是 Q1 失败，整链降级。"""
    defects = []
    if not (streak_result or {}).get('ok'):
        return None, [f"连涨候选不可用: {(streak_result or {}).get('reason', '未知')}"]

    snap_path = evidence_dir / f'node1_{trade_date}.json'
    if not snap_path.exists():
        return None, [f'节点1快照缺失: {snap_path.name}（节点1失败或尚未运行）']
    try:
        snapshot = json.loads(snap_path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        return None, [f'节点1快照不可解析: {exc}']
    news_refs = snapshot.get('news_refs')
    if not isinstance(news_refs, list):
        defects.append('node1_snapshot_missing_news_refs: 旧格式快照，无近似兜底')
        return None, defects
    if snapshot.get('news_count') != len(news_refs):
        defects.append(f"news_count({snapshot.get('news_count')}) 与 news_refs({len(news_refs)}) 不一致")
    selected = ((snapshot.get('result') or {}).get('news_screening') or {}).get('selected') or []
    ref_ids = set()
    for i, item in enumerate(news_refs):
        ref = item.get('ref')
        if not isinstance(ref, int) or isinstance(ref, bool):
            defects.append(f'news_refs[{i}] 缺整数 ref')
        elif ref in ref_ids:
            defects.append(f'news_refs ref 重复: {ref}')
        else:
            ref_ids.add(ref)
    if defects:
        return None, defects

    candidates = streak_result.get('verified_candidates') or []
    if not candidates:
        return None, ['verified_candidates 为空']
    return {
        'snapshot': snapshot, 'news_refs': news_refs, 'ref_ids': ref_ids,
        'selected': selected, 'candidates': candidates,
        'signal_date': streak_result['date'],
        'snapshot_hash': _sha256_file(snap_path),
    }, []


def _prescreen(trade_date, inputs):
    """脚本预评分（复用 score_prescreen）。返回 (scored, unresolved, meta)。"""
    scope = {'dataset_versions': streak_versions(inputs)}
    dsn = rising_streak.DSN.read_text().strip()
    sids = [score_prescreen.with_suffix(c['code']) for c in inputs['candidates']]
    with psycopg.connect(dsn, connect_timeout=5) as conn:
        bars = score_prescreen.load_bars(conn, scope, sids, inputs['signal_date'])
    scored, unresolved = [], []
    for c in inputs['candidates']:
        entry, reason = score_prescreen.score_candidate(
            c, bars.get(score_prescreen.with_suffix(c['code']), []), inputs['signal_date'])
        if entry:
            scored.append(entry)
        else:
            unresolved.append({'code': c['code'], 'name': c.get('name'), 'reason': reason})
    scored.sort(key=lambda e: -e['script_score'])
    return scored, unresolved


def streak_versions(inputs):
    return inputs['snapshot'].get('dataset_versions') or inputs.get('dataset_versions') \
        or inputs['streak_versions']


def _load_digests(entries, inputs, focus):
    """批量加载历史摘要（P1.2 分档：focus=False 250 日 / True 750 日）。
    返回 {code: digest}；单票库错误不扩散——记 error 字段，由 prompt 如实告知模型。"""
    if history_digest is None:
        return {e['code']: {'error': 'history_digest 模块不可用'} for e in entries}
    scope = {'dataset_versions': streak_versions(inputs)}
    dsn = rising_streak.DSN.read_text().strip()
    out = {}
    with psycopg.connect(dsn, connect_timeout=5) as conn:
        for e in entries:
            sid = score_prescreen.with_suffix(e['code'])
            try:
                out[e['code']] = history_digest.build_digest(
                    conn, scope, sid, inputs['signal_date'], focus=focus)
            except Exception as exc:
                out[e['code']] = {'error': f'摘要生成失败: {exc}'}
    return out


def _digest_text(d):
    """摘要 → prompt 文本。fields 见 history_digest.build_digest。"""
    if not d or d.get('error'):
        return f"长期摘要不可用（{(d or {}).get('error', '未知')}）"
    win = d.get('data_window') or ['?', '?']
    parts = [f"窗口 {win[0]}~{win[1]}（{d.get('actual_bars')} 根）"]
    for n in (250, 120, 60, 20):
        v = d.get(f'ret_{n}d_pct')
        if v is not None:
            parts.append(f"{n}日收益 {v}%")
    if d.get('max_drawdown_pct') is not None:
        parts.append(f"窗口最大回撤 {d['max_drawdown_pct']}%")
    if d.get('volatility_60d_pct') is not None:
        parts.append(f"60日波动率 {d['volatility_60d_pct']}pp")
    if d.get('avg_turnover_20d_pct') is not None:
        parts.append(f"20日均换手 {d['avg_turnover_20d_pct']}%")
    parts.append(f"停牌 {d.get('suspension_days', 0)} 天")
    parts.append(f"状态 {d.get('security_status', '?')}")
    return '；'.join(parts)


def _digest_quality(d):
    """完整性字段 → decision JSON data_quality（不进 prompt，入 Q1 证据链）。"""
    if not d or d.get('error'):
        return {'digest_error': (d or {}).get('error', '未知')}
    return {'digest_bars': d.get('actual_bars'),
            'digest_window': d.get('data_window'),
            'missing_days': len(d.get('missing_days') or []),
            'duplicate_days': d.get('duplicate_days'),
            'suspension_days': d.get('suspension_days'),
            'missing_days_check': d.get('missing_days_check')}


def _q1_range_check(scored):
    """分项分范围硬检查：A≤25 B≤20 C≤20 D≤20 E≤0，总分 ∈ [-25,110]。"""
    defects = []
    for e in scored:
        s = e['scores']
        if not (0 <= s['A'] <= 25 and 0 <= s['B'] <= 20 and 0 <= s['C'] <= 20
                and 0 <= s['D'] <= 20 and s['E'] <= 0):
            defects.append(f"{e['code']} 分项越界: {s}")
        if not (-25 <= e['script_score'] <= 110):
            defects.append(f"{e['code']} 总分越界: {e['script_score']}")
    return defects


# ---------- 模型逐只评审 ----------

def _news_pack_text(inputs):
    """节点1精选包文本（证据边界）。ref 标题取自快照 news_refs。"""
    title_by_ref = {r['ref']: r.get('title', '') for r in inputs['news_refs']}
    lines = []
    for s in inputs['selected']:
        try:
            ref = int(s.get('ref'))
        except (TypeError, ValueError):
            continue
        lines.append(
            f"ref {ref}｜{s.get('column', '?')}｜{s.get('direction', '?')}｜"
            f"证据强度 {s.get('evidence_strength', '?')}\n"
            f"  标题: {title_by_ref.get(ref, '?')}\n"
            f"  影响: {s.get('impact', '')}")
    return '\n'.join(lines) if lines else '（节点1精选为空）'


def _scorecard_text(entry):
    s, ind = entry['scores'], entry['indicators']
    return (
        f"代码 {entry['code']}（{entry['name']}）\n"
        f"连涨 {entry['days']} 天｜复合涨幅 {entry['gain_pct']}%｜累计换手 {entry['turnover_pct']}%"
        f"｜量比 {entry['volume_ratio']}\n"
        f"脚本分项：A趋势 {s['A']}/25，B动量 {s['B']}/20，C量能 {s['C']}/20，"
        f"D技术位置 {s['D']}/20，E技术惩罚 {s['E']}\n"
        f"指标：MA5/10/20/60={ind['ma5']}/{ind['ma10']}/{ind['ma20']}/{ind['ma60']}，"
        f"距60日高点 {ind['dist_high60_pct']}%，偏离MA20 {ind['dev_ma20_pct']}%，"
        f"连涨期日涨幅波动率 {ind['volatility_pct']}pp，涨停 {ind['limit_up_count']} 次，"
        f"单日涨>7%={'是' if ind['big_up_day'] else '否'}，"
        f"巨量长上影={'是' if ind['volume_upper_shadow'] else '否'}")


_REVIEW_SCHEMA = """{
  "code": "候选代码",
  "e_news": {"score": 0, "reason": "个股利空判定与依据（无利空=0；合法取值 0/-5/-10）"},
  "f_catalyst": {"score": 0, "news_refs": [3], "reason": "催化/退潮判定，-10~+10 整数，必须引用新闻 ref"},
  "bear_case": {
    "argument": "该股最强看空理由（具体到该股，禁止套话）",
    "evidence_ref": ["支撑证据：新闻 ref 编号或评分卡指标名"],
    "verdict": "holds|partially_holds|not_holds",
    "action": "剔除|降级|保留"
  },
  "confidence": "low|medium|high"
}"""


def _build_review_prompt(entry, pack_text, digest_txt):
    return f"""你是 A 股连涨候选股的逐只评审员。只评审这一只，输出严格 JSON。

## 候选评分卡（脚本预评，score_rules v1.0）
{_scorecard_text(entry)}

## 长期位置（250 日历史摘要，普通档）
{digest_txt}

## 新闻证据包（节点1精选，你唯一的消息面证据边界，禁止编造包外事实）
{pack_text}

## 任务
1. E 消息面：该股**个股层面**利空（减持/立案/业绩暴雷/监管处罚等），
   无利空=0，一般利空=-5，重大利空=-10；
2. F 新闻催化：证据包内与该股直接相关的催化（+）或退潮/利空（-），
   -10~+10 整数，无关=0，必须引用 news_refs 编号；
3. bear_case：写出该股最强看空理由（具体到该股，禁止套话；评分卡指标
   与 250 日长期位置均可作为依据），并自评该理由是否成立（verdict）。

## 输出（严格 JSON，无其他文字）
{_REVIEW_SCHEMA}"""


def _build_recheck_prompt(entry, pack_text, digest_txt):
    """Q2 复核：只看原始指标+750日长期摘要（重点档）+证据包，
    不给脚本分项分与任何主评分结论（防锚定）。"""
    ind = entry['indicators']
    return f"""你是独立复核员。只看以下证据，对该候选给出方向结论，输出严格 JSON。
你没有看过、也不要猜测任何既有评分结论。

## 候选原始指标
代码 {entry['code']}（{entry['name']}）
连涨 {entry['days']} 天｜复合涨幅 {entry['gain_pct']}%｜累计换手 {entry['turnover_pct']}%
｜量比 {entry['volume_ratio']}
MA5/10/20/60={ind['ma5']}/{ind['ma10']}/{ind['ma20']}/{ind['ma60']}，
距60日高点 {ind['dist_high60_pct']}%，偏离MA20 {ind['dev_ma20_pct']}%，
波动率 {ind['volatility_pct']}pp，涨停 {ind['limit_up_count']} 次，
巨量长上影={'是' if ind['volume_upper_shadow'] else '否'}

## 长期位置（750 日历史摘要，重点档）
{digest_txt}

## 新闻证据包（唯一的证据边界，禁止编造包外事实）
{pack_text}

## 输出（严格 JSON）
{{"code": "{entry['code']}", "direction": "recommend|not_recommend",
  "reason": "一句话理由", "confidence": "low|medium|high"}}"""


def _validate_review(entry, result, ref_ids):
    """模型评审输出 schema 校验；返回 defect 或 None。ref 越界 = 非法输出。"""
    if result.get('code') != entry['code']:
        return f"code 不匹配: {result.get('code')}"
    try:
        e = int(result['e_news']['score'])
        f = int(result['f_catalyst']['score'])
    except (KeyError, TypeError, ValueError):
        return 'e_news/f_catalyst 缺分数或不可转整数'
    if e not in E_VALUES:
        return f'e_news.score 非法: {e}（合法 {E_VALUES}）'
    if not (-10 <= f <= 10):
        return f'f_catalyst.score 越界: {f}'
    bear = result.get('bear_case') or {}
    if not isinstance(bear.get('argument'), str) or not bear['argument'].strip():
        return 'bear_case.argument 为空'
    if bear.get('verdict') not in ('holds', 'partially_holds', 'not_holds'):
        return f"bear verdict 非法: {bear.get('verdict')}"
    for ref in result['f_catalyst'].get('news_refs') or []:
        try:
            if int(ref) not in ref_ids:
                return f'f_catalyst 引用越界 ref: {ref}'
        except (TypeError, ValueError):
            return f'f_catalyst ref 非整数: {ref}'
    return None


def _call_llm_json(call_llm, prompt):
    """调用模型并解析 JSON；返回 (dict, error)。call_llm 由 preopen_report 注入
    （=call_deepseek，继承 500s 超时 + 20s 退避重试 + 备用 key，不复制该逻辑）。"""
    result = call_llm(prompt)
    if not isinstance(result, dict) or result.get('error'):
        return None, f"调用失败: {(result or {}).get('error', '未知')}"
    return result, None


# ---------- 主流程 ----------

def run_p0a8(trade_date, streak_result, *, evidence_dir, decision_dir, call_llm,
             dataset_versions=None):
    """P0.8 主入口。返回 {'ok': bool, 'reason': str|None, 'decision': dict|None,
    'board_md': str|None}。整链失败时 ok=False，调用方回退旧榜单并如实标注。"""
    evidence_dir, decision_dir = Path(evidence_dir), Path(decision_dir)
    decision_dir.mkdir(parents=True, exist_ok=True)

    inputs, defects = _load_inputs(trade_date, streak_result, Path(evidence_dir))
    if defects:
        return {'ok': False, 'reason': '; '.join(defects), 'decision': None, 'board_md': None}
    inputs['streak_versions'] = dataset_versions or streak_result.get('dataset_versions')

    # 脚本预评分 + Q1 范围检查
    scored, unresolved = _prescreen(trade_date, inputs)
    q1_defects = _q1_range_check(scored)
    if q1_defects:
        return {'ok': False, 'reason': 'Q1 分数范围硬检查失败: ' + '; '.join(q1_defects[:5]),
                'decision': None, 'board_md': None}

    # 阈值预筛 + Top40 护栏
    eligible = [e for e in scored if e['script_score'] >= PRESCREEN_THRESHOLD]
    below_threshold = len(scored) - len(eligible)
    review_pool = eligible[:REVIEW_CAP]
    guardrail_dropped = max(0, len(eligible) - len(review_pool))

    pack_text = _news_pack_text(inputs)

    # 长期证据分档（P1.2 接线）：评审池全员 250 日普通档摘要
    digests = _load_digests(review_pool, inputs, focus=False) if review_pool else {}

    # 逐只模型评审（小并发）
    reviews, review_failed = {}, []
    if review_pool:
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futures = {pool.submit(_call_llm_json, call_llm,
                                   _build_review_prompt(e, pack_text,
                                                        _digest_text(digests.get(e['code'])))): e
                       for e in review_pool}
            for fut in as_completed(futures):
                entry = futures[fut]
                try:
                    result, err = fut.result()
                except Exception as exc:  # 线程内未预见异常：fail-closed，不吞
                    result, err = None, f'评审线程异常: {exc}'
                if err:
                    review_failed.append({'code': entry['code'], 'reason': err})
                    continue
                defect = _validate_review(entry, result, inputs['ref_ids'])
                if defect:
                    review_failed.append({'code': entry['code'], 'reason': f'schema 非法: {defect}'})
                    continue
                reviews[entry['code']] = result

    # 合成总分 + bear 处理 + Q2 触发器
    ranked = []
    bear_excluded = []
    for entry in review_pool:
        rv = reviews.get(entry['code'])
        if rv is None:
            continue
        e_score = int(rv['e_news']['score'])
        f_score = int(rv['f_catalyst']['score'])
        total = entry['script_score'] + e_score + f_score
        if not (-25 <= total <= 110):
            review_failed.append({'code': entry['code'], 'reason': f'合成总分越界: {total}'})
            continue
        bear = rv['bear_case']
        verdict = bear['verdict']
        demotion = 0
        if verdict == 'holds':
            bear_excluded.append({'code': entry['code'], 'name': entry['name'],
                                  'argument': bear['argument'], 'total': total})
            continue
        if verdict == 'partially_holds':
            demotion = BEAR_DEMOTION
        total_adj = total + demotion
        need_recheck = (verdict == 'partially_holds'
                        or REVIEW_BAND[0] <= total_adj <= REVIEW_BAND[1]
                        or (e_score < 0 < f_score))
        ranked.append({
            'entry': entry, 'review': rv, 'total': total, 'demotion': demotion,
            'total_adj': total_adj, 'need_recheck': need_recheck,
        })

    # Q2 独立复核（小并发；不看主评分；触发者为重点候选，附 750 日摘要）
    recheck_failed = []
    triggered = [r for r in ranked if r['need_recheck']]
    if triggered:
        digests750 = _load_digests([r['entry'] for r in triggered], inputs, focus=True)
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futures = {pool.submit(_call_llm_json, call_llm,
                                   _build_recheck_prompt(r['entry'], pack_text,
                                                         _digest_text(digests750.get(
                                                             r['entry']['code'])))): r
                       for r in triggered}
            for fut in as_completed(futures):
                r = futures[fut]
                try:
                    result, err = fut.result()
                except Exception as exc:
                    result, err = None, f'复核线程异常: {exc}'
                direction = (result or {}).get('direction') if not err else None
                if direction not in ('recommend', 'not_recommend'):
                    recheck_failed.append({'code': r['entry']['code'],
                                           'reason': err or f"复核输出无效: {direction}"})
                    r['review_status'] = 'failed'
                    continue
                main_positive = r['total_adj'] >= QUALIFY_LINE
                consistent = (direction == 'recommend') == main_positive
                r['review_status'] = 'passed' if consistent else 'failed'
                r['recheck'] = {'direction': direction, 'reason': result.get('reason', ''),
                                'consistent': consistent}

    # Q3 机会门槛 + Top10（上限非配额；复核失败/bea剔除不进推荐）
    qualified = [r for r in ranked
                 if r['total_adj'] >= QUALIFY_LINE and r.get('review_status') != 'failed']
    qualified.sort(key=lambda r: (-r['total_adj'], r['entry']['code']))
    top = qualified[:TOP_N]
    review_partial = [{'code': r['entry']['code'], 'name': r['entry']['name'],
                       'total_adj': r['total_adj']}
                      for r in ranked if r.get('review_status') == 'failed']

    # decision JSON（口径红线：candidates=复核后推荐清单；初筛数只在 pre_filter_count）
    prompt_hashes = {r['entry']['code']: _sha256_text(
        _build_review_prompt(r['entry'], pack_text,
                             _digest_text(digests.get(r['entry']['code']))))
        for r in ranked}
    packet_material = json.dumps({
        'trade_date': trade_date, 'rule_version': RULE_VERSION,
        'snapshot_hash': inputs['snapshot_hash'],
        'prompt_hashes': dict(sorted(prompt_hashes.items())),
    }, ensure_ascii=False, sort_keys=True)
    candidates_out = []
    for i, r in enumerate(top, 1):
        entry, rv = r['entry'], r['review']
        candidates_out.append({
            'rank': i, 'code': entry['code'], 'name': entry['name'],
            'select_price': next((c.get('close') for c in inputs['candidates']
                                  if c['code'] == entry['code']), None),
            'streak_days': entry['days'],
            'composite_gain_pct': entry['gain_pct'],
            'turnover_pct': entry['turnover_pct'], 'volume_ratio': entry['volume_ratio'],
            'scores': {'A': entry['scores']['A'], 'B': entry['scores']['B'],
                       'C': entry['scores']['C'], 'D': entry['scores']['D'],
                       'E': int(rv['e_news']['score']) + entry['scores']['E'],
                       'F': int(rv['f_catalyst']['score']),
                       'total': r['total'], 'bear_demotion': r['demotion'],
                       'total_adj': r['total_adj']},
            'rating': _rating(r['total_adj']),
            'bear_case': {'argument': rv['bear_case']['argument'],
                          'verdict': rv['bear_case']['verdict']},
            'review_status': r.get('review_status') or ('passed' if r['need_recheck'] else 'none'),
            'qualification_status': 'uncalibrated',
            'data_quality': _digest_quality(digests.get(entry['code'])),
        })

    decision = {
        'trade_date': trade_date, 'data_date': inputs['signal_date'],
        'rule_version': RULE_VERSION, 'gate_version': GATE_VERSION,
        'fee_model_version': 'fee-v1.0',
        'packet_hash': _sha256_text(packet_material),
        'snapshot_hash': inputs['snapshot_hash'],
        'node1_input_hash': inputs['snapshot'].get('input_hash'),
        'model': inputs['snapshot'].get('model'),
        'reasoning_effort': inputs['snapshot'].get('reasoning_effort'),
        'generated_at': datetime.now(CST).isoformat(),
        'pre_filter_count': len(inputs['candidates']),
        'prescreen': {'scored': len(scored), 'unresolved': unresolved,
                      'threshold': PRESCREEN_THRESHOLD, 'below_threshold': below_threshold,
                      'eligible': len(eligible), 'review_cap': REVIEW_CAP,
                      'guardrail_dropped': guardrail_dropped},
        'review': {'called': len(review_pool), 'succeeded': len(reviews),
                   'failed': review_failed, 'recheck_triggered': len(triggered),
                   'recheck_failed': recheck_failed},
        'bear_excluded': bear_excluded,
        'review_partial': review_partial,
        'qualify_line': QUALIFY_LINE, 'qualified_count': len(qualified),
        'candidates': candidates_out,
        'no_qualified_note': None if top else '本日无达到门槛的候选',
    }
    out_path = decision_dir / f'decision_{trade_date}.json'
    _atomic_write_json(out_path, decision)

    return {'ok': True, 'reason': None, 'decision': decision,
            'decision_path': str(out_path), 'board_md': _render_board(decision)}


# ---------- 报告榜单渲染 ----------

def _render_board(decision):
    """评分榜单 + 空头审查 + 落选摘要 markdown（数字均来自本模块回执=[程序计算]）。"""
    pre, rv = decision['prescreen'], decision['review']
    lines = []
    lines.append(
        f"> [程序计算] 候选 {decision['pre_filter_count']} 只 → 预筛评分 {pre['scored']} 只"
        f"（unresolved {len(pre['unresolved'])}）→ 阈值≥{pre['threshold']} 过线 {pre['eligible']} 只"
        f" → 模型评审 {rv['called']} 只（护栏截断 {pre['guardrail_dropped']} 只照常回填）"
        f" → 复核触发 {rv['recheck_triggered']} 只 → 过合格线 {decision['qualified_count']} 只")
    if decision['candidates']:
        lines.append('')
        lines.append('| 名次 | 代码 | 名称 | A | B | C | D | E | F | 总分 | 评级 | 复核 |')
        lines.append('|---|---|---|---|---|---|---|---|---|---|---|---|')
        for c in decision['candidates']:
            s = c['scores']
            dem = f"（bear降级{s['bear_demotion']}→{s['total_adj']}）" if s['bear_demotion'] else ''
            lines.append(
                f"| {c['rank']} | {c['code']} | {c['name']} | {s['A']} | {s['B']} | {s['C']} "
                f"| {s['D']} | {s['E']} | {s['F']} | {s['total']}{dem} | {c['rating']}"
                f"（未校准） | {c['review_status']} |")
    else:
        lines.append('')
        lines.append(f"**{decision['no_qualified_note'] or '本日无达到门槛的候选'}**"
                     '（合格线 65 为冻结值，未校准）')
    if decision['bear_excluded']:
        lines.append('')
        lines.append('**空头审查剔除**（[模型判断] verdict=holds，由后续名次递补）：')
        for b in decision['bear_excluded']:
            lines.append(f"- {b['code']} {b['name']}（总分 {b['total']}）：{b['argument']}")
    if decision['review_partial']:
        lines.append('')
        lines.append('**复核不一致/无效（待人工确认，已移出推荐）**：')
        for p in decision['review_partial']:
            lines.append(f"- {p['code']} {p['name']}（调整后总分 {p['total_adj']}）")
    if rv['failed']:
        lines.append('')
        lines.append('**评审失败（fail-closed，不置零不估算）**：')
        for f in rv['failed']:
            lines.append(f"- {f['code']}：{f['reason']}")
    lines.append('')
    lines.append('**落选摘要**（[程序计算]，全部照常参与回填）：'
                 f"阈值下 {pre['below_threshold']} 只；护栏截断 {pre['guardrail_dropped']} 只；"
                 f"过线未进 Top10 {decision['qualified_count'] - len(decision['candidates'])} 只")
    return '\n'.join(lines)


def render_coverage_md(snapshot):
    """栏目覆盖说明段（引用节点1 coverage 声明；[模型判断]，证据边界=节点1精选包）。"""
    screening = ((snapshot.get('result') or {}).get('news_screening') or {})
    coverage = screening.get('coverage') or []
    if not coverage:
        return '> 节点1未提供栏目覆盖声明（旧格式结果或节点1失败）'
    column_cn = {'macro': '宏观与政策', 'overseas': '海外市场', 'industry': '行业与主题热点',
                 'microstructure': '市场微观结构', 'stock_event': '个股重大事件'}
    status_cn = {'ok': '✅ 有合格事件', 'no_significant_event': '当日无重要动态（正常）',
                 'collection_gap': '⚠️ 采集缺口'}
    lines = ['| 栏目 | 状态 | 说明 |', '|---|---|---|']
    for c in coverage:
        lines.append(f"| {column_cn.get(c.get('column'), c.get('column', '?'))} "
                     f"| {status_cn.get(c.get('status'), c.get('status', '?'))} "
                     f"| {c.get('reason', '')} |")
    dropped = screening.get('dropped_summary')
    if dropped:
        lines.append(f"\n> 未入选条目总体原因：{dropped}")
    return '\n'.join(lines)
