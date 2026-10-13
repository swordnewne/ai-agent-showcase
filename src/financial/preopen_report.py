#!/usr/bin/env python3
"""
盘前资讯报告生成器 v1.0

流程：
1. 拉取市场数据（纳指期货/汇率/美股个股）
2. 拉取 161130 最新净值
3. 爬取国内新闻（24h窗口）
4. 计算估算净值和盘前参考偏离率
5. DeepSeek 分析 → 结构化输出
6. 生成 Markdown 报告并推送

非目标：不连接交易账户、不下单、不自动调仓
"""

import os
import sys
import json
import time
import hashlib
import requests
import importlib.util
import tempfile
import shutil
from datetime import datetime, timezone, timedelta
from pathlib import Path

# 添加路径
# 注意: parents[3] = workspace 根目录（financial -> src -> showcase -> workspace）
WORKSPACE = os.environ.get("NIKO_WORKSPACE") or str(Path(__file__).resolve().parents[3])
sys.path.insert(0, os.path.join(WORKSPACE, "showcase", "src", "financial"))
sys.path.insert(0, os.path.join(WORKSPACE, "skills", "automation", "scrapling-adapter"))

from market_data import fetch_all_market_data
from nav_fetcher import fetch_nav, fetch_fund_price
from premium_history import save_record, multi_window_percentile
from preopen_runs import record_run, today_str, previous_news_cutoff
from trade_calendar import get_trade_date

# 新闻窗口深模块（P0.5 阶段 B 切换，2026-10-12）；导入失败自动回退旧内核
try:
    from news_window import get_news_window as _nw_get_news_window
except ImportError:
    _nw_get_news_window = None
from llm_config import load_llm_config as _load_llm_config  # 轨道 1：LLM 配置单一权威加载器
from fx_history import record_fx, get_fx_prev, fx_change_ratio
from market_session import get_session
from us_market import cumulative_index_ratio
from estimate_log import log_estimate, reconcile
from subscription_watch import watch_line as sub_watch_line
from rising_streak import fetch_candidates, render_section

try:
    import p0a8_review  # P0.8 评分链路（2026-10-13 起）；模块缺失时整链降级不阻断报告
except ImportError:
    p0a8_review = None

def _write_decision_packet(*, trade_date, slot, market_data, nav_data,
                           nav_calc, news_list, news_window, ai_result, streak_result,
                           report_path, run_id, base_status, base_reason):
    packet = {
        "schema_version": "niko-finance-decision-v1",
        "trade_date": trade_date,
        "slot": slot,
        "run_id": run_id,
        "created_at": datetime.now(timezone(timedelta(hours=8))).isoformat(),
        "report_path": str(report_path),
        "market_data": market_data,
        "nav_data": nav_data,
        "nav_calc": nav_calc,
        "news": news_list,
        "news_window": news_window,
        "deepseek_news_analysis": ai_result,
        "baseline_candidates": streak_result,
        "base_status": base_status,
        "base_reason": base_reason,
        "scoring_contract": {
            "final_decision_owner": "niko",
            "deepseek_role": "news_semantics_only",
            "baseline_score_role": "objective_market_features_only",
        },
    }
    canonical = json.dumps(packet, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":"), default=str)
    packet_hash = "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    packet["packet_hash"] = packet_hash
    path = DECISION_PACKET_DIR / f"finance_{trade_date}_{slot.replace(':', '')}.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(packet, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    temporary.replace(path)
    return path, packet_hash

def _load_deepseek_config():
    """加载 DeepSeek 配置（环境变量或 .env 文件），含备用 key。
    2026-10-12 起解析逻辑收敛到 llm_config.load_llm_config（轨道 1），
    签名与返回值保持 tuple 不变，调用方零改动。"""
    cfg = _load_llm_config(env_paths=(
        Path(WORKSPACE) / "showcase" / ".env",
        Path("/root/.openclaw/workspace/showcase/.env"),
    ))
    return cfg.api_key, cfg.base_url, cfg.model, cfg.backup_key, cfg.reasoning_effort

# 报告输出目录
REPORTS_DIR = Path(WORKSPACE) / "reports" / "preopen"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)
POSTMARKET_REPORTS_DIR = Path(WORKSPACE) / "reports" / "postmarket"
POSTMARKET_REPORTS_DIR.mkdir(parents=True, exist_ok=True)

# 桌面镜像：报告生成后复制到 Windows 桌面 niko每日金融/<preopen|postmarket>/（v1.14 定规）
DESKTOP_MIRROR_BASE = Path("/mnt/c/Users/smk/Desktop/niko每日金融")


def _mirror_report_to_desktop(report_path: Path, session: dict) -> None:
    """复制报告到桌面镜像目录。镜像失败只告警，不阻断报告主流程。"""
    subdir = "postmarket" if session.get("phase") == "post_market" else "preopen"
    target_dir = DESKTOP_MIRROR_BASE / subdir
    target_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(report_path, target_dir / report_path.name)

NEWS_EVIDENCE_DIR = REPORTS_DIR / "evidence"
NEWS_EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
DECISION_PACKET_DIR = REPORTS_DIR / "decision_packets"
DECISION_PACKET_DIR.mkdir(parents=True, exist_ok=True)


def report_path_for_session(trade_date: str, session: dict) -> Path:
    """Return a stable path whose name matches the report's market phase."""
    if session.get("phase") == "post_market":
        return POSTMARKET_REPORTS_DIR / f"postmarket_{trade_date}.md"
    return REPORTS_DIR / f"preopen_{trade_date}.md"

# 161130 前收盘价（需手动更新或从某处抓取）
# 第一版先硬编码，后续从深交所抓取
FUND_161130_PREV_CLOSE = 4.545  # 昨天的收盘价


def get_a_share_trade_date() -> str:
    """获取当前A股交易日（走交易日历，含法定节假日休市）"""
    return get_trade_date()


def _collect_news_snapshot(trade_date: str) -> dict:
    """Collect the four sources through the isolated probe, with no AI/write path."""
    probe = Path(WORKSPACE) / "finance-adapter" / "real_news_collect_probe.py"
    collector = Path(WORKSPACE) / "finance-adapter" / "finance_p0a1" / "news_fetcher.staging.py"
    if probe.exists() and collector.exists():
        spec = importlib.util.spec_from_file_location("finance_news_probe", probe)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.collect_probe(str(collector), str(NEWS_EVIDENCE_DIR / f"news_{trade_date}.jsonl"))
    raise FileNotFoundError("四来源新闻采集探针或适配器不存在")


def _parse_news_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone(timedelta(hours=8)))


def fetch_news_window(trade_date: str, cutoff_at: datetime | None = None) -> tuple[list, dict]:
    """新闻窗口入口。2026-10-12 起内核切换为 news_window 深模块（P0.5 阶段 B）；
    模块导入失败时自动回退旧内核 _fetch_news_window_legacy（保留一行回滚）。"""
    if _nw_get_news_window is None:
        return _fetch_news_window_legacy(trade_date, cutoff_at)
    tz = timezone(timedelta(hours=8))
    if cutoff_at is None:
        cutoff_at = datetime.now(tz)
    if cutoff_at.tzinfo is None:
        raise ValueError("news cutoff must include timezone")
    cutoff_at = cutoff_at.astimezone(tz)
    previous, previous_source = previous_news_cutoff(trade_date)
    start_at = _parse_news_time(previous) if previous else cutoff_at - timedelta(days=7)
    if start_at is None:
        start_at = datetime.fromisoformat(f"{previous[:10]}T00:00:00+08:00")
    if previous_source == "bootstrap":
        start_at = datetime.combine(start_at.date(), datetime.min.time(), tzinfo=tz)
    trade_date_path = NEWS_EVIDENCE_DIR / f"news_{trade_date}.jsonl"
    collection = None
    try:
        collection = _collect_news_snapshot(trade_date)
    except Exception as exc:
        print(f"四来源新闻补采失败: {type(exc).__name__}: {exc}")
    if collection:
        cutoff_at = _parse_news_time(collection.get("fetched_at")) or datetime.now(tz)
    items, meta = _nw_get_news_window(trade_date, cutoff_at, evidence_dir=NEWS_EVIDENCE_DIR, start_at=start_at)
    for item in items:
        item.setdefault("media", item.get("source_id") or item.get("source") or "未知来源")
    window = {
        "start_at": start_at.isoformat(), "cutoff_at": cutoff_at.isoformat(),
        "previous_cutoff_source": previous_source,
        "collection_status": (collection or {}).get("status") or ("previous_collection_snapshot" if trade_date_path.exists() else "unavailable"),
        "collection_errors": (collection or {}).get("errors", []),
        "news_window_counts": meta.get("counts", {}),
        "overflow_guard_triggered": bool(meta.get("overflow_guard_triggered")),
    }
    return items, window


def _fetch_news_window_legacy(trade_date: str, cutoff_at: datetime | None = None) -> tuple[list, dict]:
    """Read deduplicated daily archives since the last completed preopen cutoff."""
    tz = timezone(timedelta(hours=8))
    if cutoff_at is None:
        cutoff_at = datetime.now(tz)
    if cutoff_at.tzinfo is None:
        raise ValueError("news cutoff must include timezone")
    cutoff_at = cutoff_at.astimezone(tz)
    previous, previous_source = previous_news_cutoff(trade_date)
    start_at = _parse_news_time(previous) if previous else cutoff_at - timedelta(days=7)
    if start_at is None:
        start_at = datetime.fromisoformat(f"{previous[:10]}T00:00:00+08:00")
    if previous_source == "bootstrap":
        start_at = datetime.combine(start_at.date(), datetime.min.time(), tzinfo=tz)
    trade_date_path = NEWS_EVIDENCE_DIR / f"news_{trade_date}.jsonl"
    collection = None
    try:
        collection = _collect_news_snapshot(trade_date)
    except Exception as exc:
        print(f"四来源新闻补采失败: {type(exc).__name__}: {exc}")
    if collection:
        cutoff_at = _parse_news_time(collection.get("fetched_at")) or datetime.now(tz)

    rows_by_identity = {}
    current = start_at.date()
    while current <= cutoff_at.date():
        path = NEWS_EVIDENCE_DIR / f"news_{current.isoformat()}.jsonl"
        if path.exists():
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    try:
                        item = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(item, dict) or not item.get("title"):
                        continue
                    available = _parse_news_time(item.get("available_at") or item.get("collected_at") or item.get("collector_time") or item.get("time"))
                    if available is None or available < start_at or available > cutoff_at:
                        continue
                    raw_published_at = str(item.get("published_at_raw") or item.get("publication_time_raw") or item.get("published_at") or item.get("ctime") or item.get("display_time") or "")
                    item["published_at_raw"] = raw_published_at
                    item.setdefault("published_at_status", "verified" if item.get("publication_time_verified") else "unverified" if raw_published_at else "missing")
                    item["media"] = item.get("media") or item.get("source_id") or item.get("source", "未知来源")
                    source = str(item.get("source_id") or item.get("source") or "unknown")
                    url = str(item.get("source_url") or item.get("url") or "").strip()
                    item_id = str(item.get("source_item_id") or "").strip()
                    identity_key = "url:" + url if url else "id:" + item_id if item_id else "title:" + str(item.get("title"))
                    content_hash = str(item.get("content_hash") or "").strip()
                    if not content_hash:
                        content_hash = "sha256:" + hashlib.sha256(json.dumps(
                            {"title": item.get("title", ""), "intro": item.get("intro", "")},
                            ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                        ).encode("utf-8")).hexdigest()
                    identity = (source, identity_key, content_hash)
                    rows_by_identity.setdefault(identity, item)
        current += timedelta(days=1)
    window = {
        "start_at": start_at.isoformat(), "cutoff_at": cutoff_at.isoformat(),
        "previous_cutoff_source": previous_source,
        "collection_status": (collection or {}).get("status") or ("previous_collection_snapshot" if trade_date_path.exists() else "unavailable"),
        "collection_errors": (collection or {}).get("errors", []),
    }
    return list(rows_by_identity.values()), window


def calculate_estimated_nav(nav_data: dict, market_data: dict, prev_close_override: float = None, price_source: str = "hardcoded", fx_prev_override: float = None, session: dict = None, price_date: str = None, index_ratio: float = 1.0, index_info: dict = None) -> dict:
    """
    估算 161130 盘前净值
    
    公式：
    估算净值 = 最新官方净值 × (纳指期货最新 / 纳指期货昨收) × (汇率最新 / 汇率昨收)
    
    注意：这是近似公式，实际基金净值还受现金仓位、跟踪误差等影响
    """
    nav = nav_data.get("nav")
    nav_date = nav_data.get("nav_date")
    
    if not nav or not nav_date:
        return {"error": "缺少官方净值数据"}
    
    nq = market_data.get("nasdaq", {})
    fx = market_data.get("usd_cny", {})
    
    nq_latest = nq.get("latest")
    nq_prev = nq.get("prev_close")
    fx_latest = fx.get("latest")
    fx_prev = fx_prev_override  # 来自 fx_history 积累，不猜新浪字段布局
    
    if not nq_latest or not nq_prev:
        return {"error": "缺少纳指期货数据", "nav": nav, "nav_date": nav_date}
    
    # 纳指变化率
    nq_change = nq_latest / nq_prev
    
    # 汇率变化（如有）
    fx_change = 1.0
    fx_source = "unavailable"
    if fx_latest and fx_prev:
        _ratio = fx_change_ratio(fx_latest, fx_prev)
        if _ratio is not None:
            fx_change = _ratio
            fx_source = "history"
    
    # 三因子估算：
    #   1) index_ratio —— 从净值基准日到报告日之间【已收盘】美股场次的累计变动。
    #      这是确定性数据（已经发生的行情），早期实现整个漏算了这一段。
    #   2) nq_change  —— 报告日当日尚未收盘那一场的期货隐含变动。
    #   3) fx_change  —— 汇率变动。
    estimated = nav * (index_ratio or 1.0) * nq_change * fx_change

    # 净值新鲜度（防呆：QDII 净值有滞后，过旧必须提示）
    stale_days = None
    try:
        stale_days = (datetime.now() - datetime.strptime(nav_date, "%Y-%m-%d")).days
    except Exception:
        pass
    
    # 盘前参考偏离率（基于前收盘价）
    prev_close = prev_close_override or FUND_161130_PREV_CLOSE
    deviation = (prev_close / estimated - 1) * 100 if estimated > 0 else None
    
    # 券商口径溢价率 = 价格 / 最新公布净值 − 1
    #
    # 权威依据（HaoETF 等专业 QDII 数据站，逐项验算过）:
    #   最新溢价 = 现价 / 最新公布净值 − 1     ← 本项，券商口径
    #   实时溢价 = 现价 / 实时估值   − 1     ← 下面那个估算口径
    # 两者分母不同：本项用基金公司实际披露的净值，不带任何估算。
    broker_premium = None
    if prev_close and nav and nav > 0:
        broker_premium = (prev_close / nav - 1) * 100

    # 配对检查：QDII 净值披露有滞后，价格与净值可能不同日
    # 同日配对才是严格意义的溢价率，不同日需在报告中标注
    paired = bool(price_date and nav_date and price_date == nav_date)

    return {
        "broker_premium_pct": round(broker_premium, 2) if broker_premium is not None else None,
        "broker_premium_paired": paired,
        "price_date": price_date,
        "nav_date": nav_date,
        "index_ratio": index_ratio,
        "index_info": index_info or {},
        "official_nav": nav,
        "official_nav_date": nav_date,
        "nasdaq_futures": nq_latest,
        "nasdaq_prev": nq_prev,
        "usd_cny": fx_latest,
        "fx_prev": fx_prev,
        "fx_change": round(fx_change, 6),
        "fx_source": fx_source,
        "estimated_nav": round(estimated, 4),
        "estimated_range_low": round(estimated * 0.995, 4),
        "estimated_range_high": round(estimated * 1.005, 4),
        "prev_close": prev_close,
        "prev_close_source": price_source,
        "session": session or {},
        "deviation_pct": round(deviation, 2) if deviation else None,
        "nav_stale_days": stale_days,
        "note": "估算值仅供参考，实际净值以基金公司公布为准",
    }


def _format_news_ref_line(idx: int, news: dict) -> str:
    """格式化投递给模型的单条新闻行（编号+来源+标题+正文截 160 字）。

    本函数是"模型实际所见新闻行"的唯一生成处：build_deepseek_prompt 与
    节点 1 证据快照的 news_refs 都经它生成，保证 ref 序号与内容哈希严格对齐。
    """
    line = f"{idx}. [{news.get('media', '未知来源')}] {news.get('title', '')}"
    intro = (news.get("intro") or "").strip().replace("\n", " ")[:160]
    if intro:
        line += f" —— {intro}"
    return line


def build_deepseek_prompt(market_data: dict, nav_calc: dict, news_list: list) -> str:
    """构建 DeepSeek 分析 Prompt"""
    
    # 美股数据摘要
    nq = market_data.get("nasdaq", {})
    stocks = market_data.get("stocks", {})
    fx = market_data.get("usd_cny", {})
    
    stock_summary = []
    for sym, info in stocks.items():
        if "error" not in info:
            stock_summary.append(f"{sym}: {info.get('latest')} ({info.get('change_pct', 0):+.2f}%)")
    
    # 新闻摘要（2026-10-13 P0.6 接生产：全量正文投递——标题+正文摘要，正文截 160 字防 token 膨胀；
    # 新华社/政府网归档无正文字段，投递标题即全量；排序与 400 条护栏由 news_window 内核负责）
    news_summary = []
    for i, news in enumerate(news_list, 1):
        news_summary.append(_format_news_ref_line(i, news))
    
    # 161130 数据
    nav_text = f"""
官方净值: {nav_calc.get('official_nav')} (日期: {nav_calc.get('official_nav_date')})
估算净值: {nav_calc.get('estimated_nav')} (区间: {nav_calc.get('estimated_range_low')}-{nav_calc.get('estimated_range_high')})
前收盘价: {nav_calc.get('prev_close')}
盘前参考偏离率: {nav_calc.get('deviation_pct')}%
说明: {nav_calc.get('note')}
"""
    
    prompt = f"""你是一位盘前资讯分析师。基于以下数据，生成结构化的盘前分析报告。

## 隔夜海外市场数据
- 纳指期货: {nq.get('latest')} (昨收: {nq.get('prev_close')})
- 美元兑人民币: {fx.get('latest')}
- 美股七巨头:
{chr(10).join(stock_summary)}

## 161130 基金数据
{nav_text}

## 国内新闻（最近24小时，按时间核验状态与发布时间排序，后部的"时间未知"条目仅作背景参考）
{chr(10).join(news_summary)}

## 输出要求（严格JSON格式）

```json
{{
    "market_summary": "隔夜海外市场一句话摘要",
    "fund_161130": {{
        "direction": "positive|neutral|negative|uncertain",
        "drivers": ["影响因素1", "影响因素2"],
        "premium_risk": "溢价率风险评估",
        "confidence": "low|medium|high"
    }},
    "a_share_scenarios": [
        {{
            "sector": "板块名称",
            "direction": "positive|neutral|negative|uncertain",
            "catalysts": ["催化因素"],
            "verification_conditions": ["开盘后验证条件"],
            "invalidation_conditions": ["失效条件"],
            "confidence": "low|medium|high"
        }}
    ],
    "news_screening": {{
        "selected": [
            {{
                "ref": "新闻序号（上文列表编号）",
                "column": "macro|overseas|industry|microstructure|stock_event",
                "event_type": "事件类型",
                "direction": "positive|neutral|negative",
                "impact": "对下一交易日A股哪个板块/方向有何影响（一句话）",
                "evidence_strength": "strong|medium|weak"
            }}
        ],
        "coverage": [
            {{
                "column": "macro|overseas|industry|microstructure|stock_event",
                "status": "ok|no_significant_event|collection_gap",
                "reason": "一句话理由"
            }}
        ],
        "dropped_summary": "未入选条目的总体原因一句话"
    }},
    "facts": [
        {{
            "claim": "事实陈述",
            "source": "来源媒体"
        }}
    ],
    "missing_data": ["缺失的数据项"],
    "risk_notes": ["风险提示"]
}}
```

规则：
- 不要给出具体买卖建议（"买入"/"卖出"/"必然上涨"）
- 板块影响使用"可能有利/可能承压/影响有限/需观察"
- 所有数据必须标注来源
- 缺失数据要诚实说明
- news_screening.selected 只列答得上"对下一交易日 A 股哪个板块/方向有影响"的条目（精选非配额，答不上就不列）；ref 必须对应上文新闻列表编号
- 五栏目（macro=宏观与政策 / overseas=海外市场 / industry=行业与主题热点 / microstructure=市场微观结构 / stock_event=个股重大事件）每个都必须在 coverage 中声明状态：有合格事件=ok；有原料但无值得说的事件=no_significant_event（属正常当日状态，如实写，不许凑数）；原料缺失=collection_gap
- 指数口径：估算与「指数修正」统一采用**纳斯达克100（.NDX）**为基准。
  若基金公司未正式披露跟踪指数，missing_data 中请写
  「跟踪指数未经基金公司披露确认（估算按纳指100口径）」，
  **不要**把跟踪指数整体列为缺失——那与报告实际采用的口径自相矛盾
"""
    
    return prompt


def call_deepseek(prompt: str, max_tokens: int = 6000, _retry: bool = True) -> dict:
    """
    调用 DeepSeek API

    修复记录 (2026-09-20):
      - max_tokens 从 2000 提到 6000。中文 JSON 输出会在 2000 撞顶
        （completion_tokens 卡在 1980/2000），截断后解析失败 → 状态退化为 partial。
      - 开启 JSON 模式，减少 markdown 包裹和解释文字。
      - 遇截断/解析失败自动提高上限重试一次。
    """
    api_key, base_url, model, backup_key, reasoning_effort = _load_deepseek_config()

    if not api_key:
        return {"error": "DeepSeek API Key 未配置"}

    # 思考模式下 reasoning_tokens 计入 completion 计费与截断判定
    # （2026-10-13 实测：reasoning ~10.7k + 正文 ~3.5k，6000/12000 两档都会截断）
    if reasoning_effort != "none" and max_tokens < 16000:
        max_tokens = 16000

    def _do_request(key: str):
        """用指定 key 发起一次请求，返回 (result, auth_error)"""
        try:
            url = f"{base_url}/chat/completions"
            headers = {
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json"
            }
            # 思考模式显式化（2026-10-13）：V4 默认开启但依赖平台默认值不可控；
            # effort=none 时显式关闭。temperature 在思考模式下被平台忽略（官方文档），保留无害。
            data = {
                "model": model,
                "messages": [
                    {"role": "system", "content": "你是一位专业的盘前资讯分析师，输出严格JSON格式。"},
                    {"role": "user", "content": prompt}
                ],
                "temperature": 0.3,
                "max_tokens": max_tokens,
                "response_format": {"type": "json_object"},
                "thinking": {"type": "disabled" if reasoning_effort == "none" else "enabled"},
            }
            if reasoning_effort != "none":
                data["reasoning_effort"] = reasoning_effort

            # 思考模式 + 晨峰拥塞实测：低谷 ~72s，08:05 高峰 >180s（2026-10-13 读超时事故）。
            # 超时放宽至 360s 并允许一次退避重试；请求本身幂等，成功前不落任何状态。
            r = None
            last_exc = None
            for attempt in (1, 2):
                try:
                    r = requests.post(url, headers=headers, json=data, timeout=500)
                    break
                except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
                    last_exc = exc
                    print(f"  [警告] 第 {attempt} 次请求超时/连接异常: {exc}")
                    if attempt == 1:
                        print("  [重试] 20 秒后退避重试一次")
                        time.sleep(20)
            if r is None:
                raise last_exc
            r.raise_for_status()
            
            result = r.json()
            choice = result["choices"][0]
            content = choice["message"]["content"]
            finish_reason = choice.get("finish_reason")
            
            # 清洗 markdown 代码块
            content = content.strip()
            if content.startswith("```json"):
                content = content[7:]
            elif content.startswith("```"):
                content = content[3:]
            if content.endswith("```"):
                content = content[:-3]
            content = content.strip()
            
            # 截断检测（根因）
            if finish_reason == "length":
                usage = result.get("usage", {})
                print(f"  [警告] 响应被截断 (completion={usage.get('completion_tokens')}/{max_tokens})")
                if _retry:
                    print(f"  [重试] max_tokens 提升至 {max_tokens * 2}")
                    return call_deepseek(prompt, max_tokens=max_tokens * 2, _retry=False), False
                return {"error": "响应被 max_tokens 截断"}, False
            
            try:
                return json.loads(content), False
            except json.JSONDecodeError as e:
                print(f"  [警告] JSON解析失败: {e}")
                if _retry:
                    print(f"  [重试] 重新请求，max_tokens={max_tokens * 2}")
                    return call_deepseek(prompt, max_tokens=max_tokens * 2, _retry=False), False
                return {"error": f"JSON解析失败: {e}"}, False
        
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response is not None else None
            if status in (401, 402, 403):
                return {"error": f"HTTP {status}（认证/余额问题）"}, True
            return {"error": str(e)}, False
        except Exception as e:
            return {"error": str(e)}, False
    
    result, auth_error = _do_request(api_key)
    if auth_error and backup_key and backup_key != api_key:
        print(f"  [切换] 主 key 认证/余额失败，切换备用 key 重试")
        result, _ = _do_request(backup_key)
    
    return result


def generate_markdown_report(trade_date: str, market_data: dict, nav_data: dict, nav_calc: dict, ai_result: dict, news_list: list, streak_result: dict = None, news_window: dict = None, p0a8: dict = None) -> str:
    """生成 Markdown 报告"""
    
    nq = market_data.get("nasdaq", {})
    dj = market_data.get("dow", {})
    sp = market_data.get("sp500", {})
    fx = market_data.get("usd_cny", {})
    stocks = market_data.get("stocks", {})
    a_share = market_data.get("a_share", {})
    
    # 美股摘要
    stock_lines = []
    for sym, info in stocks.items():
        if "error" not in info:
            stock_lines.append(f"- {sym}: {info.get('latest')} ({info.get('change_pct', 0):+.2f}%)")
    
    # A股前一日数据
    a_shanghai = a_share.get("shanghai", {})
    a_shenzhen = a_share.get("shenzhen", {})

    def _index_row(info: dict):
        """返回 (收盘点位, 涨跌幅字符串)"""
        close = info.get("latest")
        prev = info.get("prev_close")
        try:
            close_f = float(close)
        except (TypeError, ValueError):
            return "N/A", "N/A"
        try:
            prev_f = float(prev)
            if prev_f > 0:
                return f"{close_f:.2f}", f"{(close_f / prev_f - 1) * 100:+.2f}%"
        except (TypeError, ValueError):
            pass
        return f"{close_f:.2f}", "N/A"

    sh_close, sh_pct = _index_row(a_shanghai)
    sz_close, sz_pct = _index_row(a_shenzhen)
    
    # 新闻列表（取前10条）
    news_lines = []
    for i, news in enumerate(news_list[:10], 1):
        news_lines.append(f"{i}. [{news.get('media', '未知来源')}] {news.get('title', '')}")
    
    # 数据新鲜度防呆
    stale_days = nav_calc.get("nav_stale_days")
    staleness_warning = ""
    if stale_days is not None and stale_days > 5:
        staleness_warning = (
            f"\n> 🚨 **数据陈旧提示**：最新净值日期距今 **{stale_days} 天**，"
            f"估算净值可能已明显偏离实际，请谨慎参考。\n"
        )

    # AI 分析结果
    fund_161130 = ai_result.get("fund_161130", {})
    scenarios = ai_result.get("a_share_scenarios", [])
    facts = ai_result.get("facts", [])
    missing = ai_result.get("missing_data", [])
    risks = ai_result.get("risk_notes", [])
    required_missing = list(ai_result.get("required_missing", []))
    optional_missing = list(ai_result.get("optional_missing", []))
    source_counts = {}
    for item in news_list:
        source = item.get("source_id") or item.get("source") or "unknown"
        source_counts[source] = source_counts.get(source, 0) + 1
    news_source_line = "、".join(f"{k} {v} 条" for k, v in sorted(source_counts.items())) or "无"
    verified_news = sum(1 for item in news_list if item.get("publication_time_verified") or item.get("published_at_status") == "verified")
    missing_news_time = sum(1 for item in news_list if (item.get("published_at_status") == "missing") or not (item.get("published_at_raw") or item.get("publication_time_raw") or item.get("published_at") or item.get("ctime") or item.get("display_time")))
    news_time_line = f"发布时间已核验 {verified_news}/{len(news_list)} 条；未验证 {len(news_list) - verified_news - missing_news_time} 条；缺失 {missing_news_time} 条"
    news_window = news_window or {}
    window_line = f"分析窗口：{news_window.get('start_at', '未知')} 至 {news_window.get('cutoff_at', '未知')}（{news_window.get('previous_cutoff_source', '未知')}）"
    quality_notes = []
    if nav_calc.get("prev_close_source") == "fallback":
        quality_notes.append("场内价格使用回退值")
    if nav_calc.get("fx_source") == "unavailable":
        quality_notes.append("汇率前值缺失")
    if (nav_calc.get("nav_stale_days") or 0) > 5:
        quality_notes.append(f"净值陈旧 {nav_calc.get('nav_stale_days')} 天")
    if not (streak_result or {}).get("ok"):
        quality_notes.append("连涨候选日线供应商不可用")
    if not (nav_calc.get("index_info") or {}).get("ok"):
        quality_notes.append("净值基准日至报告日的纳指收盘修正不可用")
    if required_missing:
        data_quality_line = "核心数据部分缺失"
    elif quality_notes:
        data_quality_line = "核心字段完整；存在质量/时效提示"
    else:
        data_quality_line = "核心字段完整"
    
    # 板块情景
    scenario_lines = []
    for s in scenarios:
        scenario_lines.append(f"""
**{s.get('sector', '未知板块')}** — {s.get('direction', 'uncertain')}
- 催化: {', '.join(s.get('catalysts', []))}
- 验证: {', '.join(s.get('verification_conditions', []))}
- 失效: {', '.join(s.get('invalidation_conditions', []))}
- 置信度: {s.get('confidence', 'medium')}
""")
    
    session = nav_calc.get("session") or {}
    report_title = session.get("title", "盘前交易简报")

    # 历史分位点（多窗口 + 平稳性提示）
    #
    # 单一窗口不可靠：两年数据显示溢价中枢单调上升（制度切换），
    # 同一个 4.69% 在近30日窗口是 73% 分位、全样本却是 92%。
    # 用券商口径溢价率作分母 —— 与回填历史序列同口径。
    pct_info = {}
    try:
        _base = nav_calc.get("broker_premium_pct")
        if _base is None:
            _base = nav_calc.get("deviation_pct")
        if _base is not None:
            pct_info = multi_window_percentile(_base) or {}
    except Exception:
        pct_info = {}

    # 券商口径溢价率
    _bp = nav_calc.get("broker_premium_pct")
    broker_pct = f"{_bp}%" if _bp is not None else "N/A"

    # 指数修正说明（已收盘美股场次的累计变动）
    _ii = nav_calc.get("index_info") or {}
    _ir = nav_calc.get("index_ratio") or 1.0
    if _ii.get("ok") and _ii.get("sessions"):
        index_line = (
            f"{( _ir - 1) * 100:+.3f}%"
            f"（纳指100 {_ii.get('from_date')} → {_ii.get('to_date')}，"
            f"{len(_ii.get('sessions') or [])} 个已完成场次） |"
        )
    elif _ii.get("ok"):
        index_line = "无需修正（净值基准日即最新） |"
    else:
        index_line = f"⚠️ 不可用（{_ii.get('reason', '未知')}） |"

    # 申购政策监控（恢复申购是前瞻信号：套利通道重开 → 溢价大概率向下）
    try:
        sub_line = sub_watch_line()
    except Exception as _e:
        sub_line = f"⚠️ 监控失败（{_e}）"
    if nav_calc.get("broker_premium_paired"):
        pair_note = f"同日配对 ✅（价格与净值均为 {nav_calc.get('nav_date')}）"
    else:
        pair_note = (
            f"⚠️ 非同源：价格 {nav_calc.get('price_date') or '?'} / "
            f"净值 {nav_calc.get('nav_date') or '?'}（QDII 净值披露滞后所致）"
        )

    if pct_info.get("has_data") and pct_info.get("windows"):
        _parts = " / ".join(
            f"{w['label']} {w['below_pct']:.0f}%" for w in pct_info["windows"]
        )
        percentile_line = f"| 历史分位 | {_parts} |\n"
        if pct_info.get("regime_note"):
            percentile_line += f"| 平稳性 | {pct_info['regime_note']} |\n"
    else:
        percentile_line = ""
    applicable = session.get("applicable", "")
    price_label = session.get("price_label", "前收盘价")
    dev_label = session.get("deviation_label", "盘前参考偏离率")

    # 纳指100 现货收盘（隔夜已完成场次）
    # 此前现货数据只用于计算「指数修正」，未在报告里呈现——导致隔夜段只有期货，
    # 读者看不到当晚真实收盘价（2026-09-21 修正）。复用 _index_row，与 A 股指数同口径。
    _ndx_to = _ii.get("to_close")
    if _ii.get("ok") and _ndx_to is not None and _ii.get("to_date") != _ii.get("from_date"):
        ndx_close, ndx_pct = _index_row({"latest": _ndx_to, "prev_close": _ii.get("from_close")})
        ndx_note = f"（{_ii.get('to_date')} 收盘场次）"
    else:
        ndx_close, ndx_pct, ndx_note = "N/A", "N/A", ""

    # P0.8 评分榜单与栏目覆盖（2026-10-13）：榜单数字全部来自 decision 回执（[程序计算]），
    # 看空理由/覆盖说明为 [模型判断]；链路降级时如实标注，候选池以上节为准。
    if p0a8 and p0a8.get("ok"):
        p0a8_block = p0a8.get("board_md") or '> 评分链路无输出'
    elif p0a8 is None:
        p0a8_block = '> 本场次未运行评分链路（盘后场次沿用当日盘前 decision，或模块不可用）'
    else:
        p0a8_block = f"> ⚠️ 评分链路降级：{p0a8.get('reason', '未知')}（候选池以上节为准，照常回填）"
    if p0a8_review is not None and "error" not in (ai_result or {}):
        coverage_block = p0a8_review.render_coverage_md({"result": ai_result})
    else:
        coverage_block = '> 节点1未运行或失败，无栏目覆盖声明'

    report = f"""# {report_title} ({trade_date})

> 报告生成时间: {datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M')}
> 生成时段: {session.get('phase_cn', '未知')}
> {applicable}
> 数据完整度: {data_quality_line}

---

## 一、隔夜海外市场 [程序计算]

| 指标 | 数值 |
|------|------|
| **纳指100 现货收盘** | **{ndx_close}** ({ndx_pct}) {ndx_note} |
| 纳指期货 | {nq.get('latest', 'N/A')} (昨收: {nq.get('prev_close', 'N/A')}) |
| 道指期货 | {dj.get('latest', 'N/A')} (昨收: {dj.get('prev_close', 'N/A')}) |
| 标普期货 | {sp.get('latest', 'N/A')} (昨收: {sp.get('prev_close', 'N/A')}) |
| 美元兑人民币 | {fx.get('latest', 'N/A')} |

### 美股七巨头
{chr(10).join(stock_lines) if stock_lines else '- 数据缺失'}

---

## 二、161130 基金分析 [程序计算]（AI分析小节为 [模型判断]）

| 指标 | 数值 |
|------|------|
| 最新官方净值 | {nav_calc.get('official_nav', 'N/A')} ({nav_calc.get('official_nav_date', 'N/A')}) |
| {price_label} | {nav_calc.get('prev_close', 'N/A')} ({'实时抓取' if nav_calc.get('prev_close_source') == 'sina' else '⚠️ 回退值'}) |
| **溢价率（券商口径）** | **{broker_pct}** |
| 配对情况 | {pair_note} |
| 估算净值 | {nav_calc.get('estimated_nav', 'N/A')} |
| 指数修正 | {index_line}
| 申购政策 | {sub_line} |
| {dev_label}（估算口径） | {nav_calc.get('deviation_pct', 'N/A')}% |
| 净值新鲜度 | {nav_calc.get('nav_stale_days', 'N/A')} 天前 |
{percentile_line}

> ⚠️ 说明: {nav_calc.get('note', '')}
{staleness_warning}

### AI分析
- 方向: {fund_161130.get('direction', 'uncertain')}
- 驱动因素: {', '.join(fund_161130.get('drivers', []))}
- 溢价风险: {fund_161130.get('premium_risk', 'N/A')}
- 置信度: {fund_161130.get('confidence', 'medium')}

---

## 三、A股前一日收盘 [程序计算]

| 指数 | 收盘 | 涨跌 |
|------|------|------|
| 上证指数 | {sh_close} | {sh_pct} |
| 深证成指 | {sz_close} | {sz_pct} |

> 注：显示的是前一交易日收盘数据，用于判断大盘趋势

---

## 四、A股板块情景 [模型判断]

{chr(10).join(scenario_lines) if scenario_lines else '- 暂无分析'}

---

## 五、主板连续收涨候选 Top 10 [程序计算]

{render_section(streak_result or {'ok': False, 'reason': '未运行筛选'})}

---

## 六、评分榜单（P0.8 主评分 + 风险闸门）

{p0a8_block}

---

## 七、栏目覆盖说明 [模型判断]（证据边界 = 节点1精选包）

{coverage_block}

---

## 八、国内新闻摘要 [程序计算]

- 本次新闻采集来源：{news_source_line}；窗口内共 {len(news_list)} 条。
- {window_line}；当日采集快照状态：{(news_window or {}).get('collection_status', 'unknown')}
- 新闻字段状态：{news_time_line}。

{chr(10).join(news_lines) if news_lines else '- 暂无新闻'}

---

## 九、数据缺失项

### 核心数据
{chr(10).join(['- ' + m for m in required_missing]) if required_missing else '- 无'}

### 可选增强项
{chr(10).join(['- ' + m for m in optional_missing] + (['- ' + m for m in missing if m not in required_missing and m not in optional_missing] if missing else [])) if (optional_missing or missing) else '- 无'}

---

## 十、风险提示

{chr(10).join(['- ' + r for r in risks]) if risks else '- 本报告不构成投资建议'}
- 溢价率（券商口径）= 价格 ÷ 最新公布净值 − 1，与历史序列同口径，可直接比较
- {dev_label}（估算口径）已将隔夜期货/汇率变动计入分母，属前瞻估算，非已实现溢价，不参与历史分位
- 净值披露滞后时价格与净值非同源，已在「配对情况」中标注
- 估算净值基于纳指期货，实际净值以基金公司公布为准
"""
    
    return report


def run(slot: str = "manual", ignore_calendar: bool = False, defer_niko: bool = False) -> dict:
    """
    主流程

    Returns:
        {"status": complete|partial|failed|skipped, "report_path": path|None, "reason": str|None}
    """
    print("=" * 50)
    print("盘前资讯报告生成器 v1.0")
    print("=" * 50)
    
    # 1. 判断交易日
    if ignore_calendar:
        trade_date = today_str()
        print(f"\n交易日(忽略日历): {trade_date}")
    else:
        trade_date = get_a_share_trade_date()
        if not trade_date:
            print("今日非A股交易日，跳过")
            return {"status": "skipped", "report_path": None, "reason": "非A股交易日"}
        print(f"\n交易日: {trade_date}")
    
    # 2. 拉取市场数据
    print("\n[1/5] 拉取市场数据...")
    market_data = fetch_all_market_data()
    _nq = market_data.get("nasdaq") or {}
    _fx = market_data.get("usd_cny") or {}
    print(f"  纳指期货: {_nq.get('latest', '失败')}")
    print(f"  汇率: {_fx.get('latest', '失败')}")
    # 3. 拉取 161130 净值
    print("\n[2/5] 拉取 161130 净值...")
    nav_data = fetch_nav()
    print(f"  净值: {nav_data.get('nav')} ({nav_data.get('nav_date')})")
    
    # 4. 计算估算净值
    print("\n[3/5] 计算估算净值...")
    # 3b. 拉取 161130 场内价格（前收盘价，用于偏离率）
    print("\n[2.5/5] 拉取 161130 场内价格...")
    price_data = fetch_fund_price()
    if "error" in price_data or not price_data.get("latest"):
        fund_prev_close = FUND_161130_PREV_CLOSE
        price_source = "fallback"
        print(f"  [警告] 价格抓取失败，回退硬编码 {FUND_161130_PREV_CLOSE}: {price_data.get('error')}")
    else:
        fund_prev_close = price_data["latest"]
        price_source = "sina"
        print(f"  最新价: {fund_prev_close} (昨收 {price_data.get('prev_close')}, {price_data.get('date')})")

    # 3c. 取汇率前值（自行积累，不猜字段布局）
    fx_prev_val, fx_prev_date = get_fx_prev(trade_date)
    if fx_prev_val:
        print(f"  汇率前值: {fx_prev_val} ({fx_prev_date})")
    else:
        print("  汇率前值: 暂无历史（首次运行，本次不计汇率变动）")

    # 3d. 判定当前交易时段（报告措辞随之变化）
    session = get_session()
    print(f"\n[时段] {session['phase_cn']} → {session['title']}")
    if session["price_role"] != "prev_close":
        print(f"  [注意] 盘中/盘后生成，最新价为{session['price_label']}，非昨日收盘")

    # 3e. 累计指数修正 —— 补齐「从净值基准日到报告日」之间已收盘的美股场次
    #
    # 净值(中国T) ↔ 纳指100(美国T)。盘前拿到的净值带有约 2 个交易日的滞宿，
    # 期间已收盘场次的涨跌是确定性数据，必须补上。
    _nav_date = nav_data.get("nav_date")
    index_info = cumulative_index_ratio(_nav_date, trade_date) if _nav_date else {"ok": False, "reason": "无净值日期"}
    if index_info.get("ok"):
        index_ratio = index_info.get("ratio", 1.0)
        if index_info.get("sessions"):
            print(f"  指数修正: {index_info['from_date']} → {index_info['to_date']} "
                  f"({(index_ratio - 1) * 100:+.3f}%, {len(index_info['sessions'])} 个已完成场次)")
        else:
            print("  指数修正: 无已完成场次（净值基准日即最新）")
    else:
        index_ratio = 1.0
        print(f"  指数修正: 不可用（{index_info.get('reason')}）—— 估算将缺失这部分变动")

    nav_calc = calculate_estimated_nav(nav_data, market_data, fund_prev_close, price_source, fx_prev_val, session, price_data.get("date"), index_ratio, index_info)
    if "error" in nav_calc:
        print(f"  计算失败: {nav_calc['error']}")
    else:
        print(f"  估算净值: {nav_calc['estimated_nav']}")
        print(f"  参考偏离率: {nav_calc['deviation_pct']}%")
    
    # 5. 抓取新闻
    print("\n[4/5] 抓取新闻...")
    news_list, news_window = fetch_news_window(trade_date)
    news_cutoff_at = datetime.fromisoformat(news_window["cutoff_at"])
    print(f"  抓取到 {len(news_list)} 条新闻")
    
    # 6. DeepSeek 分析
    print("\n[5/5] DeepSeek 分析...")
    ai_result = {}
    if news_list and "error" not in nav_calc:
        prompt = build_deepseek_prompt(market_data, nav_calc, news_list)
        ai_result = call_deepseek(prompt)
        if "error" in ai_result:
            print(f"  DeepSeek 失败: {ai_result['error']}")
        else:
            print(f"  分析完成")
            # P0.6：节点 1 证据快照落盘（input_hash 绑定输入，供 P0.7 packet 与复盘追责）
            try:
                _, _, _model_name, _, _effort = _load_deepseek_config()
                # news_refs：带序号的实际投递列表（P0.7 ref 基准；content_hash = 模型所见整行文本的 sha256，
                # 与 prompt 共用 _format_news_ref_line，杜绝快照与投递内容漂移）
                news_refs = []
                for _i, _news in enumerate(news_list, 1):
                    _line = _format_news_ref_line(_i, _news)
                    news_refs.append({
                        "ref": _i,
                        "source": _news.get("media", "未知来源"),
                        "title": _news.get("title", ""),
                        "url": str(_news.get("source_url") or _news.get("url") or ""),
                        "content_hash": "sha256:" + hashlib.sha256(_line.encode("utf-8")).hexdigest(),
                    })
                snapshot = {
                    "trade_date": trade_date,
                    "generated_at": datetime.now(timezone(timedelta(hours=8))).isoformat(),
                    "model": _model_name,
                    "reasoning_effort": _effort,
                    "input_hash": "sha256:" + hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                    "news_count": len(news_list),
                    "news_window_counts": news_window.get("news_window_counts"),
                    "news_refs": news_refs,
                    "result": ai_result,
                }
                snap_path = NEWS_EVIDENCE_DIR / f"node1_{trade_date}.json"
                snap_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=1),
                                     encoding="utf-8")
                print(f"  节点1证据快照: {snap_path.name} (input_hash {snapshot['input_hash'][:20]}…)")
            except Exception as e:
                print(f"  [警告] 节点1快照保存失败（不阻断）: {e}")
    else:
        print("  跳过（缺少数据）")
        ai_result = {"error": "缺少新闻或净值数据"}
    
    # 7. 生成报告
    print("\n[生成报告]...")
    print("\n[连涨筛选] 拉取上一交易日主板榜单...")
    # Before the open, only the previous completed A-share session is valid.
    # The 22:00 fallback runs after today's close, so its same-day board is a
    # valid completed snapshot and must not be rejected as "future" data.
    streak_expected_date = trade_date if session.get("phase") == "post_market" else None
    streak_result = fetch_candidates(trade_date, expected_date=streak_expected_date)
    print(f"  {'候选 ' + str(streak_result['total']) + ' 只' if streak_result.get('ok') else '未通过: ' + streak_result['reason']}")

    # P0.8 评分链路（2026-10-13 起）：脚本预筛(≥55) → Top40 逐只模型评审 →
    # 闸门三问 → decision_{date}.json。仅盘前场次运行；盘后兜底场次沿用当日
    # 盘前 decision，不用偏移后的新闻窗口重跑覆盖。任何失败降级为 None/ok=False，
    # 报告照常生成并在榜单段如实标注。
    p0a8 = None
    if session.get("phase") != "post_market" and p0a8_review is not None:
        print("\n[P0.8] 评分链路（预筛→逐只评审→闸门→decision JSON）...")
        try:
            p0a8 = p0a8_review.run_p0a8(
                trade_date, streak_result,
                evidence_dir=NEWS_EVIDENCE_DIR, decision_dir=DECISION_PACKET_DIR,
                call_llm=call_deepseek)
            if p0a8.get("ok"):
                d = p0a8["decision"]
                print(f"  P0.8 完成: 评审 {d['review']['called']} 只，"
                      f"过线 {d['qualified_count']} 只，推荐 {len(d['candidates'])} 只"
                      f"（{p0a8['decision_path']}）")
            else:
                print(f"  P0.8 降级: {p0a8.get('reason')}")
        except Exception as e:
            p0a8 = {"ok": False, "reason": f"未捕获异常: {e}", "board_md": None}
            print(f"  P0.8 异常降级（不阻断报告）: {e}")

    report = generate_markdown_report(trade_date, market_data, nav_data, nav_calc, ai_result, news_list, streak_result, news_window, p0a8)
    
    # 保存文件。盘后兜底不能覆盖同日盘前产物；两者是不同语义的报告。
    report_path = report_path_for_session(trade_date, session)
    fd, temporary = tempfile.mkstemp(prefix=report_path.name + ".", dir=report_path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(report)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, report_path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    print(f"  报告已保存: {report_path}")

    # 镜像到桌面 niko每日金融/（失败只告警，不阻断主流程）
    try:
        _mirror_report_to_desktop(report_path, session)
        print(f"  桌面镜像已复制: {DESKTOP_MIRROR_BASE}")
    except Exception as e:
        print(f"  桌面镜像复制失败（不阻断）: {e}")

    # 8. 保存历史溢价数据 + 汇率快照
    try:
        record_fx(trade_date, market_data.get("usd_cny", {}).get("latest"))
        print(f"  汇率快照已保存")
    except Exception as e:
        print(f"  汇率快照保存失败: {e}")

    # 8b. 估算值落库 —— 为了日后度量估算到底准不准
    try:
        _nqf, _nqp = nav_calc.get("nasdaq_futures"), nav_calc.get("nasdaq_prev")
        log_estimate({
            "target_date": trade_date,
            "slot": slot,
            "base_nav": nav_calc.get("official_nav"),
            "base_nav_date": nav_calc.get("official_nav_date"),
            "index_ratio": nav_calc.get("index_ratio"),
            "index_sessions": (nav_calc.get("index_info") or {}).get("sessions"),
            "futures_change": round(_nqf / _nqp, 6) if (_nqf and _nqp) else None,
            "fx_change": nav_calc.get("fx_change"),
            "estimated_nav": nav_calc.get("estimated_nav"),
            "price": nav_calc.get("prev_close"),
            "price_date": nav_calc.get("price_date"),
            "broker_premium_pct": nav_calc.get("broker_premium_pct"),
        })
        print("  估算值已落库")
    except Exception as e:
        print(f"  估算值落库失败: {e}")

    # 8c. 对账 —— 净值正式披露后回填真实值并计误差
    try:
        rc = reconcile()
        if rc.get("ok"):
            print(f"  对账完成: 回填 {rc.get('filled', 0)} 条真实净值")
        else:
            print(f"  对账跳过: {rc.get('reason')}")
    except Exception as e:
        print(f"  对账失败: {e}")

    if "error" not in nav_calc:
        try:
            save_record(
                trade_date=trade_date,
                nav=nav_data.get("nav"),
                price=fund_prev_close,
                premium_pct=nav_calc.get("deviation_pct"),
                nav_date=nav_data.get("nav_date")
            )
            print(f"  历史数据已保存")
        except Exception as e:
            print(f"  历史数据保存失败: {e}")
    
    # 9. 状态判定（严格幂等依据：仅 complete 算“今日已执行”）
    has_market = "error" not in market_data.get("nasdaq", {})
    has_nav = "error" not in nav_calc
    has_news = len(news_list) > 0
    has_ai = bool(ai_result) and "error" not in ai_result

    if has_market and has_nav and has_news and has_ai:
        status = "complete"
    elif has_market or has_nav:
        status = "partial"
    else:
        status = "failed"

    # 数据质量门槛：四项齐全也不代表数据可信，以下情况强制降级
    quality_issues = []
    if nav_calc.get("prev_close_source") == "fallback":
        quality_issues.append("前收盘价回退到硬编码值")
    if nav_calc.get("fx_source") == "unavailable":
        quality_issues.append("汇率前值缺失")
    # QDII 净值可因周末和法定节假日延迟披露。报告正文已经展示陈旧天数
    # 和风险提示；它本身不阻断核心字段齐全的报告。只有会改变估算可用性
    # 的缺失或回退数据才降级为 partial。
    if not streak_result.get("ok"):
        quality_issues.append(f"主板连涨榜不可用：{streak_result.get('reason', '未知原因')}")
    if not (nav_calc.get("index_info") or {}).get("ok"):
        quality_issues.append(f"指数修正不可用：{(nav_calc.get('index_info') or {}).get('reason', '未知原因')}")
    if status == "complete" and quality_issues:
        status = "partial"

    reason = None
    missing_parts = []
    if not has_market:
        missing_parts.append("市场数据")
    if not has_nav:
        missing_parts.append("161130净值")
    if not has_news:
        missing_parts.append("新闻")
    if not has_ai:
        missing_parts.append("AI分析")
    if missing_parts:
        reason = "缺失: " + "、".join(missing_parts)
    if quality_issues:
        _q = "质量: " + "、".join(quality_issues)
        reason = f"{reason} | {_q}" if reason else _q

    # 10. 落库（幂等标记）
    base_status = status
    base_reason = reason
    if defer_niko:
        status = "partial"
        reason = f"{reason} | 等待 Niko 最终评分" if reason else "等待 Niko 最终评分"
    run_id = record_run(
        run_date=trade_date, run_slot=slot, status=status, report_path=report_path,
        news_cutoff_at=news_cutoff_at.isoformat(),
    )
    packet_path = None
    packet_hash = None
    if defer_niko:
        packet_path, packet_hash = _write_decision_packet(
            trade_date=trade_date,
            slot=slot,
            run_id=run_id,
            market_data=market_data,
            nav_data=nav_data,
            nav_calc=nav_calc,
            news_list=news_list,
            news_window=news_window,
            ai_result=ai_result,
            streak_result=streak_result,
            report_path=report_path,
            base_status=base_status,
            base_reason=base_reason,
        )
        print(f"  Niko评分交接包: {packet_path} ({packet_hash})")
    print(f"\n[状态] {status} (slot={slot})")
    if reason:
        print(f"  {reason}")

    # 11. 输出摘要到控制台（供定时任务捕获）
    print("\n" + "=" * 50)
    print("报告摘要")
    print("=" * 50)

    fund = ai_result.get("fund_161130", {})
    print(f"状态: {status}")
    print(f"\n161130: {fund.get('direction', 'N/A')}")
    print(f"估算净值: {nav_calc.get('estimated_nav', 'N/A')}")
    print(f"参考偏离率: {nav_calc.get('deviation_pct', 'N/A')}%")
    print(f"\n完整报告: {report_path}")

    return {
        "status": status,
        "report_path": str(report_path),
        "reason": reason,
        "run_id": run_id,
        "packet_path": str(packet_path) if packet_path else None,
        "packet_hash": packet_hash,
    }


if __name__ == "__main__":
    run()
