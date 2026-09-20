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
import requests
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
from preopen_runs import record_run, today_str
from trade_calendar import get_trade_date
from fx_history import record_fx, get_fx_prev, fx_change_ratio
from market_session import get_session
from us_market import cumulative_index_ratio
from estimate_log import log_estimate, reconcile
from subscription_watch import watch_line as sub_watch_line

def _load_deepseek_config():
    """加载 DeepSeek 配置（环境变量或 .env 文件）"""
    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    base_url = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
    model = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
    
    if not api_key:
        # 尝试从 showcase/.env 读取
        env_candidates = [
            Path(WORKSPACE) / "showcase" / ".env",
            Path("/root/.openclaw/workspace/showcase/.env"),
        ]
        for env_file in env_candidates:
            if env_file.exists():
                with open(env_file, "r") as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#"):
                            continue
                        if line.startswith("DEEPSEEK_API_KEY="):
                            api_key = line.split("=", 1)[1]
                        elif line.startswith("DEEPSEEK_BASE_URL="):
                            base_url = line.split("=", 1)[1]
                        elif line.startswith("DEEPSEEK_MODEL="):
                            model = line.split("=", 1)[1]
                if api_key:
                    break
    
    return api_key, base_url, model

# 报告输出目录
REPORTS_DIR = Path(WORKSPACE) / "reports" / "preopen"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)

# 161130 前收盘价（需手动更新或从某处抓取）
# 第一版先硬编码，后续从深交所抓取
FUND_161130_PREV_CLOSE = 4.545  # 昨天的收盘价


def get_a_share_trade_date() -> str:
    """获取当前A股交易日（走交易日历，含法定节假日休市）"""
    return get_trade_date()


def fetch_news_24h() -> list:
    """抓取最近24小时新闻（复用现有爬虫，简化版）"""
    # 第一版：直接调用现有 news_fetcher.py 的抓取函数
    # 为简化，先用新浪API直接抓
    news_list = []
    
    try:
        # 新浪API
        url = "https://feed.mix.sina.com.cn/api/roll/get?pageid=153&lid=2516&num=30&r=12345"
        r = requests.get(url, timeout=10)
        data = r.json()
        if data.get("result", {}).get("status", {}).get("code") == 0:
            items = data.get("result", {}).get("data", [])
            cutoff = datetime.now() - timedelta(hours=24)
            for item in items:
                ctime = item.get("ctime", "")
                try:
                    pub_time = datetime.fromtimestamp(int(ctime))
                    if pub_time >= cutoff:
                        news_list.append({
                            "title": item.get("title", ""),
                            "intro": item.get("intro", ""),
                            "url": item.get("url", ""),
                            "source": "sina",
                            "media": item.get("media_name", "新浪"),
                            "publish_time": pub_time.isoformat(),
                        })
                except:
                    pass
    except Exception as e:
        print(f"新闻抓取失败: {e}")
    
    return news_list


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
    
    # 新闻摘要（取前15条）
    news_summary = []
    for i, news in enumerate(news_list[:15], 1):
        news_summary.append(f"{i}. [{news.get('media', '未知来源')}] {news.get('title', '')}")
    
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

## 国内新闻（最近24小时）
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
    api_key, base_url, model = _load_deepseek_config()
    
    if not api_key:
        return {"error": "DeepSeek API Key 未配置"}
    
    try:
        url = f"{base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        data = {
            "model": model,
            "messages": [
                {"role": "system", "content": "你是一位专业的盘前资讯分析师，输出严格JSON格式。"},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.3,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"}
        }
        
        r = requests.post(url, headers=headers, json=data, timeout=60)
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
                return call_deepseek(prompt, max_tokens=max_tokens * 2, _retry=False)
            return {"error": "响应被 max_tokens 截断"}
        
        try:
            return json.loads(content)
        except json.JSONDecodeError as e:
            print(f"  [警告] JSON解析失败: {e}")
            if _retry:
                print(f"  [重试] 重新请求，max_tokens={max_tokens * 2}")
                return call_deepseek(prompt, max_tokens=max_tokens * 2, _retry=False)
            return {"error": f"JSON解析失败: {e}"}
        
    except Exception as e:
        return {"error": str(e)}


def generate_markdown_report(trade_date: str, market_data: dict, nav_data: dict, nav_calc: dict, ai_result: dict, news_list: list) -> str:
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

    report = f"""# {report_title} ({trade_date})

> 报告生成时间: {datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M')}
> 生成时段: {session.get('phase_cn', '未知')}
> {applicable}
> 数据完整度: {'完整' if not missing else '部分缺失'}

---

## 一、隔夜海外市场

| 指标 | 数值 |
|------|------|
| 纳指期货 | {nq.get('latest', 'N/A')} (昨收: {nq.get('prev_close', 'N/A')}) |
| 道指期货 | {dj.get('latest', 'N/A')} (昨收: {dj.get('prev_close', 'N/A')}) |
| 标普期货 | {sp.get('latest', 'N/A')} (昨收: {sp.get('prev_close', 'N/A')}) |
| 美元兑人民币 | {fx.get('latest', 'N/A')} |

### 美股七巨头
{chr(10).join(stock_lines) if stock_lines else '- 数据缺失'}

---

## 二、161130 基金分析

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

## 三、A股前一日收盘

| 指数 | 收盘 | 涨跌 |
|------|------|------|
| 上证指数 | {sh_close} | {sh_pct} |
| 深证成指 | {sz_close} | {sz_pct} |

> 注：显示的是前一交易日收盘数据，用于判断大盘趋势

---

## 四、A股板块情景

{chr(10).join(scenario_lines) if scenario_lines else '- 暂无分析'}

---

## 五、国内新闻摘要

{chr(10).join(news_lines) if news_lines else '- 暂无新闻'}

---

## 六、数据缺失项

{chr(10).join(['- ' + m for m in missing]) if missing else '- 无'}

---

## 七、风险提示

{chr(10).join(['- ' + r for r in risks]) if risks else '- 本报告不构成投资建议'}
- 溢价率（券商口径）= 价格 ÷ 最新公布净值 − 1，与历史序列同口径，可直接比较
- {dev_label}（估算口径）已将隔夜期货/汇率变动计入分母，属前瞻估算，非已实现溢价，不参与历史分位
- 净值披露滞后时价格与净值非同源，已在「配对情况」中标注
- 估算净值基于纳指期货，实际净值以基金公司公布为准
"""
    
    return report


def run(slot: str = "manual", ignore_calendar: bool = False) -> dict:
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
    news_list = fetch_news_24h()
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
    else:
        print("  跳过（缺少数据）")
        ai_result = {"error": "缺少新闻或净值数据"}
    
    # 7. 生成报告
    print("\n[生成报告]...")
    report = generate_markdown_report(trade_date, market_data, nav_data, nav_calc, ai_result, news_list)
    
    # 保存文件
    report_path = REPORTS_DIR / f"preopen_{trade_date}.md"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"  报告已保存: {report_path}")
    
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
    _stale = nav_calc.get("nav_stale_days")
    if _stale is not None and _stale > 5:
        quality_issues.append(f"净值陈旧 {_stale} 天")
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
    try:
        record_run(
            run_date=trade_date,
            run_slot=slot,
            status=status,
            report_path=report_path,
        )
        print(f"\n[状态] {status} (slot={slot})")
        if reason:
            print(f"  {reason}")
    except Exception as e:
        print(f"\n[告警] 运行记录写入失败: {e}")

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

    return {"status": status, "report_path": str(report_path), "reason": reason}


if __name__ == "__main__":
    run()
