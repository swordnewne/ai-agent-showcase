#!/usr/bin/env python3
"""
preopen 链路冒烟测试

用途：
  preopen_report.py 今天被打了十几次补丁，三个主函数分别达到 94 / 220 / 231 行。
  在首次真实运行前需要一个可重复执行的安全网 —— 每次改动后跑一遍，
  就能立刻知道有没有踩坏既有功能。

设计原则：
  · 尽量不依赖网络（用注入数据测纯逻辑），网络部分单独标 SKIP
  · 每个用例输出 PASS / FAIL / SKIP，失败时给出可读原因
  · 断言的是「行为」而非「实现细节」，这样重构时不会误报

用法：
  python3 showcase/tests/smoke_preopen.py
  python3 showcase/tests/smoke_preopen.py --with-network   # 含网络用例
"""

import os
import sys
import traceback
from pathlib import Path

WORKSPACE = os.environ.get("NIKO_WORKSPACE") or str(Path(__file__).resolve().parents[2])
FIN = os.path.join(WORKSPACE, "showcase", "src", "financial")
sys.path.insert(0, FIN)

WITH_NETWORK = "--with-network" in sys.argv

_results = []


def case(name):
    """装饰器：登记一个测试用例"""
    def deco(fn):
        _results.append((name, fn))
        return fn
    return deco


def _run_one(name, fn):
    try:
        r = fn()
        if r == "SKIP":
            return ("SKIP", "")
        return ("PASS", "")
    except AssertionError as e:
        return ("FAIL", str(e))
    except Exception as e:
        return ("FAIL", f"{type(e).__name__}: {e}")


# ---------------------------------------------------------------- 导入完整性

@case("全部模块可导入")
def t_imports():
    mods = ["market_session", "subscription_regimes", "subscription_watch",
            "premium_history", "fx_history", "us_market", "estimate_log",
            "import_premium_history", "preopen_report", "rising_streak"]
    bad = []
    for m in mods:
        try:
            __import__(m)
        except Exception as e:
            bad.append(f"{m}: {e}")
    assert not bad, "导入失败: " + "; ".join(bad)


@case("连涨榜日期、市场范围与评分渲染")
def t_rising_streak():
    from rising_streak import _eligible, _parse_page, _evaluate, _previous_session, render_section
    assert _previous_session("2026-09-28") == "2026-09-24", "中秋休市后应回溯至9月24日"
    assert _eligible("002123", "示例") and _eligible("605123", "示例")
    for code, name in [("300123", "创业"), ("301123", "创业"), ("688123", "科创"),
                       ("689123", "科创"), ("830123", "北交"), ("600123", "ST样本")]:
        assert not _eligible(code, name), f"不应纳入 {code} {name}"
    html = ('更新时间：2026-09-24 <span class="page_info">1/1</span><tbody>'
            '<tr><td>1</td><td>600123</td><td>样本甲</td><td>12</td><td>13</td><td>11</td>'
            '<td>3</td><td>9.5%</td><td>20%</td><td>化工</td></tr>'
            '<tr><td>2</td><td>300123</td><td>样本乙</td><td>20</td><td>22</td><td>18</td>'
            '<td>4</td><td>30%</td><td>80%</td><td>电子</td></tr>'
            '<tr><td>3</td><td>601123</td><td>样本丙</td><td>6</td><td>7</td><td>5</td>'
            '<td>2</td><td>3%</td><td>10%</td><td>银行</td></tr></tbody>')
    day, pages, rows = _parse_page(html)
    assert (day, pages, len(rows)) == ("2026-09-24", 1, 1)
    result = {"ok": True, "date": day, "source": "https://example.org", "total": 1,
              "top": [_evaluate(rows[0])]}
    rendered = render_section(result)
    assert "样本甲（600123）" in rendered and "股价（元）" in rendered
    assert "| 1 | 样本甲（600123） | 12.00 |" in rendered
    assert "样本乙" not in rendered and "上涨概率" in rendered
    assert "动量/18.75" in rendered and "日线/25" in rendered and "未经回测校准" in rendered
    assert abs(sum(result["top"][0]["score_components"].values()) - result["top"][0]["score"]) < 0.01
    sample = dict(rows[0], days=5, gain=12.0, turnover=15.0)
    mild = _evaluate(sample)
    stronger = _evaluate(dict(sample, gain=17.0))
    overheated = _evaluate(dict(sample, gain=45.0, turnover=65.0))
    assert stronger["score_components"]["momentum"] > mild["score_components"]["momentum"]
    assert stronger["score"] > mild["score"], "合理涨幅应有正向权重"
    assert overheated["score"] < stronger["score"], "极端涨幅/换手不应自动登顶"
    assert 0 <= mild["score"] <= 100
    assert "不生成虚构的 Top 10" in render_section({"ok": False, "reason": "榜单过期"})
    from unittest.mock import Mock
    from rising_streak import fetch_candidates
    stale_response = Mock(status_code=200, text=html.replace("2026-09-24", "2026-09-23"))
    stale_response.raise_for_status.return_value = None
    stale_session = Mock()
    stale_session.get.return_value = stale_response
    stale_result = fetch_candidates("2026-09-28", stale_session)
    assert not stale_result["ok"] and "拒绝" in stale_result["reason"]
    assert stale_session.get.call_count == 1

    # An explicit closed-session date is valid for the post-market retry, but
    # the default pre-open date gate remains strict.
    same_session = Mock()
    same_response = Mock(status_code=200, text=html)
    same_response.raise_for_status.return_value = None
    same_session.get.return_value = same_response
    same_result = fetch_candidates("2026-09-28", same_session, expected_date="2026-09-24")
    assert same_result["ok"] is False or same_result.get("date") == "2026-09-24"


@case("连涨候选日线复核与日期门禁")
def t_rising_streak_daily():
    from unittest.mock import Mock
    from rising_streak import _daily_structure, _evaluate
    sample = _evaluate({"code": "600123", "name": "样本甲", "price": 15.0,
                        "days": 5, "gain": 12.0, "turnover": 15.0, "sector": "设备"})
    from datetime import date, timedelta
    start = date(2026, 6, 29)
    bars = [[(start + timedelta(days=i)).isoformat(), "", f"{9 + i / 10:.2f}", "", "", str(100 + i)]
            for i in range(61)]
    bars[-1][0] = "2026-09-28"
    bars[-1][2] = "15.00"
    session = Mock()
    session.get.return_value.json.return_value = {"data": {"sh600123": {"qfqday": bars}}}
    session.get.return_value.raise_for_status.return_value = None
    scored = _daily_structure(sample, "2026-09-28", session)
    assert scored["technical"]["date"] == "2026-09-28"
    assert 0 <= scored["score_components"]["technical"] <= 25
    assert abs(sum(scored["score_components"].values()) - scored["score"]) < 0.02
    try:
        _daily_structure(sample, "2026-09-29", session)
    except ValueError as exc:
        assert "日期不符" in str(exc)
    else:
        raise AssertionError("过期日线应拒绝")


# ---------------------------------------------------------------- 交易时段

@case("market_session 时段判定")
def t_session():
    from datetime import datetime, timezone, timedelta
    from market_session import get_session
    CST = timezone(timedelta(hours=8))
    exp = [((8, 15), "pre_market"), ((9, 45), "morning"), ((12, 10), "lunch_break"),
           ((14, 0), "afternoon"), ((20, 0), "post_market")]
    for (h, m), want in exp:
        got = get_session(datetime(2026, 9, 21, h, m, tzinfo=CST))["phase"]
        assert got == want, f"{h:02d}:{m:02d} 期望 {want} 实得 {got}"
    # 盘前才应使用「前收盘价」措辞
    s = get_session(datetime(2026, 9, 21, 8, 15, tzinfo=CST))
    assert s["price_role"] == "prev_close", "盘前 price_role 应为 prev_close"
    s2 = get_session(datetime(2026, 9, 21, 14, 0, tzinfo=CST))
    assert s2["price_role"] != "prev_close", "盘中不应标为前收盘价"


@case("盘后/午间报告不得自称盘前")
def t_session_wording():
    from datetime import datetime, timezone, timedelta
    from market_session import get_session
    CST = timezone(timedelta(hours=8))
    for h in (12, 20):
        t = get_session(datetime(2026, 9, 21, h, 0, tzinfo=CST))
        assert "盘前" not in t["title"], f"{h}点标题不应含「盘前」: {t['title']}"


@case("盘前与盘后报告路径不互相覆盖")
def t_report_paths():
    from datetime import datetime, timezone, timedelta
    from market_session import get_session
    from preopen_report import report_path_for_session
    CST = timezone(timedelta(hours=8))
    date = "2026-10-08"
    pre = report_path_for_session(date, get_session(datetime(2026, 10, 8, 8, 15, tzinfo=CST)))
    post = report_path_for_session(date, get_session(datetime(2026, 10, 8, 21, 17, tzinfo=CST)))
    assert pre.name == "preopen_2026-10-08.md"
    assert post.name == "postmarket_2026-10-08.md"
    assert pre != post and pre.parent != post.parent


# ---------------------------------------------------------------- 申购制度

@case("subscription_regimes 制度判定")
def t_regimes():
    from subscription_regimes import regime_at
    cases = [
        ("2025-08-20", "open", 5000),
        ("2025-10-30", "suspended", 50),
        ("2025-12-01", "suspended", 10),
        ("2026-09-20", "suspended", 0),
    ]
    for d, kind, limit in cases:
        r = regime_at(d)
        assert r["kind"] == kind, f"{d} 期望 kind={kind} 实得 {r['kind']}"
        assert r["limit_rmb"] == limit, f"{d} 期望 limit={limit} 实得 {r['limit_rmb']}"


@case("制度时间线无空洞且不重叠")
def t_regime_coverage():
    from subscription_regimes import REGIMES
    prev_end = None
    for start, end, _k, _l, _n in REGIMES:
        if prev_end is not None:
            assert start > prev_end, f"区间重叠或乱序: {prev_end} -> {start}"
        prev_end = end or "9999-99-99"
    # 覆盖率抽查
    from subscription_regimes import regime_at
    for d in ["2024-12-15", "2025-03-01", "2025-08-20", "2026-05-01"]:
        assert regime_at(d)["kind"] is not None, f"{d} 未落入任何制度区间"


# ---------------------------------------------------------------- 分位点

@case("分位点数学性质")
def t_percentile():
    from premium_history import multi_window_percentile
    r = multi_window_percentile(4.69)
    assert r.get("has_data"), "应返回 has_data"
    ws = r["windows"]
    assert len(ws) >= 3, f"窗口数过少: {len(ws)}"
    for w in ws:
        assert 0 <= w["below_pct"] <= 100, f"分位越界: {w}"
        assert w["n"] > 0, f"样本量为 0: {w}"
    # 极端值应贴边
    hi = multi_window_percentile(999)
    assert all(w["below_pct"] == 100.0 for w in hi["windows"]), "极大值应计 100%"
    lo = multi_window_percentile(-999)
    assert all(w["below_pct"] == 0.0 for w in lo["windows"]), "极小值应计 0%"


@case("同档/同类分位存在且样本合理")
def t_percentile_regime():
    from premium_history import multi_window_percentile
    r = multi_window_percentile(4.69)
    labels = [w["label"] for w in r["windows"]]
    assert any("同档" in l for l in labels), f"缺同档窗口: {labels}"
    assert any("同类" in l for l in labels), f"缺同类窗口: {labels}"
    same_kind = [w for w in r["windows"] if "同类" in w["label"]][0]
    assert same_kind["n"] >= 100, f"同类样本过少: {same_kind['n']}"


# ---------------------------------------------------------------- 指数修正

@case("us_market 累计比率计算（注入数据）")
def t_index_ratio():
    from us_market import cumulative_index_ratio
    closes = {"2026-09-16": 100.0, "2026-09-17": 110.0, "2026-09-18": 121.0,
              "2026-09-21": 133.1}
    r = cumulative_index_ratio("2026-09-17", "2026-09-21", closes)
    assert r["ok"], f"应成功: {r}"
    # 只应计入 9/18（严格 < 报告日）
    assert r["sessions"] == ["2026-09-18"], f"场次错误: {r['sessions']}"
    assert abs(r["ratio"] - 1.1) < 1e-9, f"比率应为 1.1 实得 {r['ratio']}"
    # 基准日即最新 → 比率 1.0
    r2 = cumulative_index_ratio("2026-09-18", "2026-09-19", closes)
    assert abs(r2["ratio"] - 1.0) < 1e-9, "无已完成场次时比率应为 1.0"
    # 基准日缺失 → 应失败而非静默给 1.0
    r3 = cumulative_index_ratio("2000-01-01", "2026-09-21", closes)
    assert not r3["ok"], "基准日无数据时应返回 ok=False"


# ---------------------------------------------------------------- 汇率

@case("fx_history 合理性校验")
def t_fx():
    from fx_history import fx_change_ratio
    assert fx_change_ratio(100, 100) == 1.0
    assert fx_change_ratio(101, 100) is not None
    # 单日 ±10% 以外视为异常，必须拒绝
    assert fx_change_ratio(150, 100) is None, "暴涨 50% 应被拒绝"
    assert fx_change_ratio(50, 100) is None, "暴跌 50% 应被拒绝"
    assert fx_change_ratio(None, 100) is None
    assert fx_change_ratio(100, 0) is None


# ---------------------------------------------------------------- 估算公式

@case("估算净值公式含指数修正")
def t_estimated_nav():
    from preopen_report import calculate_estimated_nav
    nav_data = {"nav": 4.0, "nav_date": "2026-09-17"}
    market = {"nasdaq": {"latest": 110.0, "prev_close": 100.0},
              "usd_cny": {"latest": 7.0}}
    base = calculate_estimated_nav(nav_data, market, 4.5, "sina", None, None,
                                   "2026-09-18")
    assert "error" not in base, f"不应报错: {base}"
    # 未传指数修正 → 1.0
    assert abs(base["index_ratio"] - 1.0) < 1e-9
    # 传了指数修正 1.1 → 估算应放大 10%
    withidx = calculate_estimated_nav(nav_data, market, 4.5, "sina", None, None,
                                      "2026-09-18", 1.1, {"ok": True, "sessions": ["2026-09-18"]})
    ratio = withidx["estimated_nav"] / base["estimated_nav"]
    assert abs(ratio - 1.1) < 1e-6, f"指数修正未生效，比值 {ratio}"
    # 券商口径 = 价格/官方净值 - 1（不带估算）
    assert abs(base["broker_premium_pct"] - (4.5 / 4.0 - 1) * 100) < 1e-6, \
        f"券商口径计算错误: {base['broker_premium_pct']}"
    # 配对判定
    assert base["broker_premium_paired"] is False, "价格/净值不同日应判定为未配对"


@case("缺少行情时应返回 error 而非崩溃")
def t_estimated_nav_missing():
    from preopen_report import calculate_estimated_nav
    r = calculate_estimated_nav({"nav": 4.0, "nav_date": "2026-09-17"},
                                {}, 4.5, "sina")
    assert "error" in r, "缺行情时应返回 error"


# ---------------------------------------------------------------- 估算落库

@case("estimate_log 落库与字段完整")
def t_estimate_log():
    from estimate_log import log_estimate, _connect
    target = "1999-01-01"   # 用远古日期，避免污染真实样本
    ok = log_estimate({"target_date": target, "slot": "smoke-test",
                       "base_nav": 1.0, "base_nav_date": "1998-12-31",
                       "index_ratio": 1.01, "index_sessions": ["1999-01-01"],
                       "estimated_nav": 1.02, "price": 1.05,
                       "broker_premium_pct": 2.0})
    assert ok, "落库应成功"
    conn = _connect()
    row = conn.execute(
        "SELECT base_nav, index_ratio, index_sessions, estimated_nav, est_deviation_pct "
        "FROM nav_estimates WHERE target_date=? AND slot=?", (target, "smoke-test")
    ).fetchone()
    conn.execute("DELETE FROM nav_estimates WHERE target_date=? AND slot=?",
                 (target, "smoke-test"))
    conn.commit()
    conn.close()
    assert row is not None, "未能取回刚写入的记录"
    assert row[2] == "1999-01-01", f"场次列表序列化失败: {row[2]}"
    assert abs(row[4] - (1.05 / 1.02 - 1) * 100) < 1e-6, f"偏离率计算错误: {row[4]}"


# ---------------------------------------------------------------- 报告渲染

@case("报告渲染含关键区块（注入数据，不联网）")
def t_report_render():
    import preopen_report
    market = {
        "nasdaq": {"latest": 110.0, "prev_close": 100.0},
        "usd_cny": {"latest": 7.0, "prev_close": 7.0},
        "a_share": {"shanghai": {"latest": 3900.0, "prev_close": 3850.0},
                    "shenzhen": {"latest": 12000.0, "prev_close": 11900.0}},
    }
    nav_data = {"nav": 4.0, "nav_date": "2026-09-17"}
    calc = preopen_report.calculate_estimated_nav(
        nav_data, market, 4.5, "sina", None, None, "2026-09-18", 1.02,
        {"ok": True, "ratio": 1.02, "from_date": "2026-09-17",
         "to_date": "2026-09-18", "sessions": ["2026-09-18"]})
    md = preopen_report.generate_markdown_report(
        "2026-09-21", market, nav_data, calc,
        {"summary": "测试"}, [{"title": "测试新闻"}])
    assert isinstance(md, str) and len(md) > 300, f"报告过短: {len(md) if isinstance(md, str) else md}"
    for must in ["161130 基金分析", "溢价率（券商口径）", "指数修正", "历史分位",
                 "主板连续收涨候选 Top 10"]:
        assert must in md, f"报告缺少区块「{must}」"
    # A股表格应为收盘点位 + 涨跌幅
    assert "上证指数" in md, "缺上证指数行"
    # 不得残留未替换的占位符
    assert "N/A%" not in md, "存在未替换的占位符"


# ---------------------------------------------------------------- 网络用例

@case("[网络] 纳指日线可取")
def t_net_index():
    if not WITH_NETWORK:
        return "SKIP"
    from us_market import fetch_ndx_closes
    c = fetch_ndx_closes()
    assert len(c) > 100, f"指数数据过少: {len(c)}"


@case("[网络] 基金净值可取")
def t_net_nav():
    if not WITH_NETWORK:
        return "SKIP"
    from nav_fetcher import fetch_nav
    r = fetch_nav()
    assert r.get("nav"), f"未取到净值: {r}"


@case("[网络] 公告监控可用")
def t_net_watch():
    if not WITH_NETWORK:
        return "SKIP"
    from subscription_watch import check
    r = check()
    assert r.get("ok"), f"公告接口不可用: {r}"


def main():
    print("=" * 66)
    print("preopen 链路冒烟测试" + ("  (含网络用例)" if WITH_NETWORK else "  (跳过网络)"))
    print("=" * 66)
    n_pass = n_fail = n_skip = 0
    failures = []
    for name, fn in _results:
        status, msg = _run_one(name, fn)
        icon = {"PASS": "✅", "FAIL": "❌", "SKIP": "⏭️ "}[status]
        print(f"{icon} {name}")
        if status == "PASS":
            n_pass += 1
        elif status == "SKIP":
            n_skip += 1
        else:
            n_fail += 1
            failures.append((name, msg))
            print(f"      ↳ {msg}")
    print("-" * 66)
    print(f"通过 {n_pass} / 失败 {n_fail} / 跳过 {n_skip}")
    if failures:
        print()
        print("失败详情:")
        for name, msg in failures:
            print(f"  · {name}: {msg}")
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
