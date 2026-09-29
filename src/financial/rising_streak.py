"""A股连续收涨候选观察：只使用已完成交易日的榜单，不预测收益。"""

import math
import re
import statistics
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from html.parser import HTMLParser

import requests

from trade_calendar import is_trading_day


URL = "https://data.10jqka.com.cn/rank/lxsz/"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
           "Referer": URL}
DAILY_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"


class _Table(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows = []
        self.row = None
        self.cell = None
        self.in_body = False

    def handle_starttag(self, tag, attrs):
        if tag == "tbody":
            self.in_body = True
        elif self.in_body and tag == "tr":
            self.row = []
        elif self.row is not None and tag == "td":
            self.cell = []

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if tag == "td" and self.cell is not None:
            self.row.append("".join(self.cell).strip())
            self.cell = None
        elif tag == "tr" and self.row is not None:
            self.rows.append(self.row)
            self.row = None
        elif tag == "tbody":
            self.in_body = False


def _previous_session(trade_date):
    day = date.fromisoformat(trade_date) - timedelta(days=1)
    for _ in range(15):
        if is_trading_day(day):
            return day.isoformat()
        day -= timedelta(days=1)
    raise ValueError("无法确定上一交易日")


def _eligible(code, name):
    # 沪深主板：排除创业板(300/301)、科创板(688/689)、北交所、ETF、B股及ST。
    return bool(re.fullmatch(r"(?:000|001|002|003|600|601|603|605)\d{3}", code)
                and name and "ST" not in name.upper() and "退" not in name)


def _parse_page(text):
    updated = re.search(r"更新时间：\s*(\d{4}-\d{2}-\d{2})", text)
    total = re.search(r'class="page_info"[^>]*>\s*\d+/(\d+)', text)
    if not updated or not total:
        raise ValueError("榜单缺少更新日期或分页信息")
    parser = _Table()
    parser.feed(text)
    stocks = []
    for cells in parser.rows:
        if len(cells) < 10:
            continue
        try:
            code, name = cells[1:3]
            if not _eligible(code, name):
                continue
            days = int(cells[6])
            if days < 3:
                continue
            price = float(cells[3])
            gain = float(cells[7].rstrip("%"))
            turnover = float(cells[8].rstrip("%"))
            if not all(map(math.isfinite, (price, gain, turnover))) or price <= 0 or gain <= 0 or turnover < 0:
                continue
            stocks.append({"code": code, "name": name, "price": price, "close": price,
                           "days": days, "gain": gain, "turnover": turnover, "sector": cells[9]})
        except (ValueError, IndexError):
            continue
    return updated.group(1), int(total.group(1)), stocks


def _evaluate(stock):
    days, gain, turnover = stock["days"], stock["gain"], stock["turnover"]
    # 同花顺榜单只有累计涨幅和累计换手，没有逐日波动/成交额；不能伪称测过真实稳定性。
    # 四项各自落在 [0, 权重]，无基础赠分。以下权重是明确的研究假设，未经样本外回测校准。
    daily = ((1 + gain / 100) ** (1 / days) - 1) * 100  # 等效日复合涨幅，不是实际逐日涨幅
    turnover_per_day = turnover / days  # 累计换手的日均近似，不等于每日实际换手
    momentum = max(0.0, min(25.0, gain / 18.0 * 25.0))
    persistence = max(0.0, 30.0 - abs(days - 5) * 4.0)
    liquidity = max(0.0, 20.0 - max(0.0, 1.0 - turnover_per_day) * 8.0
                    - max(0.0, turnover_per_day - 5.0) * 3.0)
    risk = max(0.0, 25.0 - max(0.0, daily - 2.5) * 5.0
               - max(0.0, gain - 25.0) * 0.7 - max(0, days - 7) * 2.0)
    components = {"momentum": round(momentum, 1), "persistence": round(persistence, 1),
                  "liquidity": round(liquidity, 1), "risk": round(risk, 1)}
    score = round(sum(components.values()), 1)
    stock = dict(stock, score=score, score_components=components,
                 daily_equivalent=round(daily, 2), turnover_per_day=round(turnover_per_day, 2))
    pace_note = ("等效日涨幅偏快，可能透支短线空间" if daily > 2.5
                 else "等效日涨幅未触发过热扣分（不代表逐日波动平稳）")
    liquidity_note = ("平均换手偏低，关注成交承接" if turnover_per_day < 1
                      else "平均换手偏高，留意分歧与回撤" if turnover_per_day > 5
                      else "平均换手位于观察区间，仍需逐日量价确认")
    stock["logic"] = (f"{stock['sector']}；{days}日累计涨{gain:.1f}%提供动量依据，"
                      f"等效日复合涨幅{daily:.2f}%：{pace_note}；"
                      f"累计换手{turnover:.1f}%／日均近似{turnover_per_day:.2f}%：{liquidity_note}；"
                      f"持续{days}日，{('连涨末端需防拥挤' if days > 7 else '观察能否维持趋势')}。"
                      "后续核对收盘价、逐日量价与公告；收盘不再创新高则连涨条件失效。")
    return stock


def _daily_structure(stock, expected, session):
    """对榜单候选作日线复核；数据缺口直接剔除，不以缺失值补高分。"""
    code = stock["code"]
    symbol = ("sh" if code.startswith("6") else "sz") + code
    response = session.get(DAILY_URL, params={"param": f"{symbol},day,,,90,qfq"}, timeout=10)
    response.raise_for_status()
    data = response.json().get("data", {}).get(symbol, {})
    bars = data.get("qfqday") or data.get("day") or []
    if len(bars) < 61 or bars[-1][0] != expected:
        raise ValueError(f"{code} 日线缺失或日期不符")
    closes = [float(b[2]) for b in bars]
    volumes = [float(b[5]) for b in bars]
    if not all(math.isfinite(x) and x > 0 for x in closes + volumes):
        raise ValueError(f"{code} 日线数值异常")
    if abs(closes[-1] / stock["price"] - 1) > 0.02:
        raise ValueError(f"{code} 榜单收盘价与日线不符")
    ma5 = statistics.mean(closes[-5:])
    ma10 = statistics.mean(closes[-10:])
    ma20 = statistics.mean(closes[-20:])
    ma60 = statistics.mean(closes[-60:])
    returns = [(closes[i] / closes[i - 1] - 1) * 100 for i in range(-5, 0)]
    volatility = statistics.pstdev(returns)
    volume_ratio = statistics.mean(volumes[-5:]) / statistics.mean(volumes[-10:-5])
    high60 = max(closes[-60:])
    above60 = closes[-1] >= ma60
    alignment = ma5 > ma10 > ma20 > ma60
    near_high = closes[-1] / high60 >= .95
    structure = (10 if alignment else 5 if above60 else 0)
    structure += 5 if near_high else max(0, 5 - (1 - closes[-1] / high60) * 30)
    rhythm = max(0, 5 - max(0, volatility - 1.5) * 1.5)
    volume_score = max(0, 5 - abs(volume_ratio - 1.3) * 3)
    technical = round(structure + rhythm + volume_score, 1)
    base = stock["score_components"]
    # 榜单四因子缩放至75分，日线结构占25分，避免凭形态覆盖基本动量和风险。
    scaled = {k: round(v * .75, 2) for k, v in base.items()}
    # 保留两位内部精度，使总分严格等于组件之和；报告展示层再按一位格式化。
    # 这样审计和回测不会因多次四舍五入产生不可解释的分数差。
    stock = dict(stock, score_components={**scaled, "technical": technical},
                 score=round(sum(scaled.values()) + technical, 2),
                 technical={"ma5": round(ma5, 2), "ma10": round(ma10, 2),
                            "ma20": round(ma20, 2), "ma60": round(ma60, 2),
                            "high60": round(high60, 2), "volatility5": round(volatility, 2),
                            "volume_ratio": round(volume_ratio, 2), "date": expected})
    stock["logic"] += (f" 日线复核：MA5/10/20/60={ma5:.2f}/{ma10:.2f}/{ma20:.2f}/{ma60:.2f}元，"
                       f"{'多头排列' if alignment else '未形成多头排列'}；距60日收盘高点{(closes[-1] / high60 - 1) * 100:.1f}%；"
                       f"近5日涨跌幅标准差{volatility:.2f}个百分点，近5日量相对前5日{volume_ratio:.2f}倍。"
                       "量比为分窗均量比，非交易软件盘中量比；技术形态不代表收益预测。")
    return stock


def fetch_candidates(trade_date, session=None):
    """盘前只允许截至上一交易日的完整榜单；日期不符明确降级，不拿旧榜冒充今日。"""
    expected = _previous_session(trade_date)
    session = session or requests.Session()
    rows, seen = [], set()
    try:
        response = session.get(URL, headers=HEADERS, timeout=10)
        response.raise_for_status()
        response.encoding = "gbk"
        updated, pages, current = _parse_page(response.text)
        if updated != expected:
            return {"ok": False, "reason": f"榜单日期{updated}，上一交易日应为{expected}，拒绝使用过期/盘中数据"}
        if not 1 <= pages <= 30:
            raise ValueError("榜单分页异常")
        for page in range(1, pages + 1):
            if page > 1:
                response = session.get(f"{URL}page/{page}/", headers=HEADERS, timeout=10)
                response.raise_for_status()
                response.encoding = "gbk"
                page_date, page_count, current = _parse_page(response.text)
                if page_date != updated or page_count != pages:
                    raise ValueError("分页日期或页数不一致")
            if not current:
                raise ValueError(f"第{page}页未解析出合格主板连涨股票，拒绝不完整榜单")
            for stock in current:
                if stock["code"] not in seen:
                    seen.add(stock["code"])
                    rows.append(_evaluate(stock))
        if not rows:
            raise ValueError("榜单没有合格股票")
        verified = []
        # 每个候选均按同一套日线指标复核；不能仅对原榜前十复核，否则会漏掉更稳的股票。
        with ThreadPoolExecutor(max_workers=5) as pool:
            futures = [pool.submit(_daily_structure, s, updated, session) for s in rows]
            for future in futures:
                try:
                    verified.append(future.result())
                except (requests.RequestException, ValueError, KeyError, IndexError, TypeError, ZeroDivisionError):
                    continue
        if len(verified) < 10:
            raise ValueError(f"日线验证仅{len(verified)}/{len(rows)}只成功，不足10只；拒绝生成Top 10")
        verified.sort(key=lambda s: (-s["score"], -s["days"], s["code"]))
        return {"ok": True, "date": updated, "source": URL, "daily_source": DAILY_URL,
                "total": len(rows), "verified": len(verified), "top": verified[:10]}
    except (requests.RequestException, ValueError) as exc:
        return {"ok": False, "reason": str(exc)}


def render_section(result):
    if not result.get("ok"):
        return f"> ⚠️ 连涨筛选不可用：{result.get('reason', '未取到数据')}。不生成虚构的 Top 10。"
    rows = [f"> 来源：[同花顺连涨榜]({result['source']})；截至 {result['date']} 收盘，"
            f"沪深主板连续收涨≥3个交易日，合格 {result['total']} 只。"
            f"日线复核 {result.get('verified', '未标注')} 只；日线来源：[腾讯行情]({result.get('daily_source', DAILY_URL)})。"
            "排名是规则化观察优先级，不是上涨概率；不含创业板、科创板、北交所、ST。",
            "", "| 排名 | 股票 | 股价（元） | 连涨 | 累计涨幅 | 累计换手 | 动量/18.75 | 持续/22.5 | 换手/15 | 风险/18.75 | 日线/25 | 总分/100 | 依据与验证 |",
            "|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|"]
    for i, s in enumerate(result["top"], 1):
        c = s.get("score_components", {})
        rows.append(f"| {i} | {s['name']}（{s['code']}） | {s['price']:.2f} | {s['days']}天 | "
                    f"{s['gain']:.1f}% | {s['turnover']:.1f}% | {c.get('momentum', 'N/A')} | "
                    f"{c.get('persistence', 'N/A')} | {c.get('liquidity', 'N/A')} | "
                    f"{c.get('risk', 'N/A')} | {c.get('technical', 'N/A')} | {s['score']:.1f} | {s['logic']} |")
    rows.append("\n> 评分口径（未经回测校准）：榜单动量18.75、持续22.5、换手15、过热风险18.75；日线结构25（均线/60日高点15、近5日波动5、近5日均量相对前5日5）。榜单原四因子按0.75折算；高分不等于未来收益期望更高。")
    rows.append("> 量价指标仅描述历史形态；财报、消息、行业催化与大盘风险尚未核查，也没有经样本外回测证明未来收益优势。需结合公告和后续收盘复核，不构成买卖建议或自动交易信号。")
    return "\n".join(rows)
