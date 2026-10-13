"""Live mainboard streaks and technical scores from the single BaoStock PG dataset."""
import hashlib
import json
import math
import os
import re
import statistics
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import psycopg
from trade_calendar import is_trading_day

FINANCE_ADAPTER = Path(__file__).resolve().parents[3] / 'finance-adapter'
if str(FINANCE_ADAPTER) not in sys.path:
    sys.path.insert(0, str(FINANCE_ADAPTER))
from finance_p0a1.baostock_current_reader import (
    CurrentBaoStockReadError,
    resolve_current_baostock_scope,
)

DATASET = 'cn-a-share-baostock-history'
SOURCE = 'baostock-daily-k-a-stock'
RULE_VERSION = 'baostock-mainboard-rising-streak-v1'
DSN = Path('/root/.config/niko/finance-shadow.dsn')
WORKSPACE = Path(os.environ.get('NIKO_WORKSPACE') or '/root/.openclaw/workspace')
CST = timezone(timedelta(hours=8))

def _previous_session(trade_date):
    day = date.fromisoformat(trade_date)-timedelta(days=1)
    for _ in range(20):
        if is_trading_day(day): return day.isoformat()
        day-=timedelta(days=1)
    raise ValueError('无法确定上一交易日')

def _score(code,name,bars,expected,dates):
    if len(bars)<61 or bars[-1]['trade_date']!=expected:
        raise ValueError('历史不足61日或末日不符')
    if [b['trade_date'] for b in bars[-61:]]!=dates[-61:]:
        raise ValueError('最近61交易日存在行情缺口')
    for b in bars:
        for key in ('close','preclose','volume','pct_chg'):
            value=b[key]
            if value is None or not math.isfinite(float(value)):
                raise ValueError('缺少真实日线字段')
        if b['close']<=0 or b['preclose']<=0 or b['volume']<0:
            raise ValueError('日线价格或成交量异常')
    days=0
    for b in reversed(bars):
        if b['trade_status']!=1 or b['pct_chg']<=0: break
        days+=1
    if days<3: raise ValueError('连涨不足3日')
    if days==len(bars): raise ValueError('连涨窗口尚未找到起点')
    streak=bars[-days:]
    if any(b['turnover_pct'] is None for b in streak):
        raise ValueError('连涨期间换手率缺失')
    gain=(math.prod(1+float(b['pct_chg'])/100 for b in streak)-1)*100
    turnover=sum(float(b['turnover_pct']) for b in streak)
    daily=((1+gain/100)**(1/days)-1)*100
    turnover_per_day=turnover/days
    base={
        'momentum':max(0,min(25,gain/18*25)),
        'persistence':max(0,30-abs(days-5)*4),
        'liquidity':max(0,20-max(0,1-turnover_per_day)*8-max(0,turnover_per_day-5)*3),
        'risk':max(0,25-max(0,daily-2.5)*5-max(0,gain-25)*.7-max(0,days-7)*2),
    }
    # Align earlier closes to the latest raw close using official ex-rights preclose.
    # This removes mechanical corporate-action gaps without claiming official qfq prices.
    closes=[0.0]*len(bars)
    closes[-1]=float(bars[-1]['close'])
    for i in range(len(bars)-1,0,-1):
        closes[i-1]=closes[i]*float(bars[i]['preclose'])/float(bars[i]['close'])
    volumes=[float(b['volume']) for b in bars]
    ma5,ma10,ma20,ma60=(statistics.mean(closes[-n:]) for n in (5,10,20,60))
    returns=[(float(b['close'])/float(b['preclose'])-1)*100 for b in bars[-5:]]
    volatility=statistics.pstdev(returns)
    previous_volume=statistics.mean(volumes[-10:-5])
    if previous_volume<=0: raise ValueError('比较窗口无有效成交量')
    volume_ratio=statistics.mean(volumes[-5:])/previous_volume
    high60=max(closes[-60:])
    alignment=ma5>ma10>ma20>ma60
    structure=10 if alignment else 5 if closes[-1]>=ma60 else 0
    structure+=5 if closes[-1]/high60>=.95 else max(0,5-(1-closes[-1]/high60)*30)
    technical=round(structure+max(0,5-max(0,volatility-1.5)*1.5)+max(0,5-abs(volume_ratio-1.3)*3),1)
    components={k:round(round(v,1)*.75,2) for k,v in base.items()}
    components['technical']=technical
    logic=(f'{days}日实际复合涨幅{gain:.2f}%，实际累计换手{turnover:.2f}%；'
        f'MA5/10/20/60={ma5:.2f}/{ma10:.2f}/{ma20:.2f}/{ma60:.2f}元，'
        f'{"多头排列" if alignment else "未形成多头排列"}；'
        f'距60日收盘高点{(closes[-1]/high60-1)*100:.1f}%；'
        f'近5日涨跌幅标准差{volatility:.2f}个百分点，近5日均量相对前5日{volume_ratio:.2f}倍。')
    return dict(code=code,name=name,price=float(bars[-1]['close']),close=float(bars[-1]['close']),
        days=days,gain=gain,turnover=turnover,sector='',score=round(sum(components.values()),2),
        score_components=components,logic=logic,technical=dict(ma5=round(ma5,2),ma10=round(ma10,2),
        ma20=round(ma20,2),ma60=round(ma60,2),high60=round(high60,2),
        volatility5=round(volatility,2),volume_ratio=round(volume_ratio,2),date=expected),
        bar_hashes=[b['row_hash'] for b in bars],source_response_hashes=sorted({b['source_response_hash'] for b in bars}))

def fetch_candidates(trade_date,session=None,expected_date=None):
    """Read completed BaoStock sessions. No HTTP request or alternative source."""
    expected=expected_date or _previous_session(trade_date)
    try:
        latest_complete=datetime.now(CST).date()
        if datetime.now(CST).hour<15: latest_complete-=timedelta(days=1)
        if date.fromisoformat(expected)>latest_complete:
            raise ValueError('请求日期尚未收盘')
        with psycopg.connect(DSN.read_text().strip(),connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
                cur.execute("SET LOCAL statement_timeout='30s'")
                scope = resolve_current_baostock_scope(conn, dataset_id=DATASET)
                versions = scope['dataset_versions']
                cur.execute('SELECT DISTINCT trade_date FROM finance.daily_bars WHERE dataset_id=%s AND dataset_version=ANY(%s)'
                    ' AND adjust_type=%s AND source_id=%s AND trade_date BETWEEN %s AND %s ORDER BY trade_date DESC LIMIT 91',
                    (DATASET,versions,'raw',SOURCE,date.fromisoformat(expected)-timedelta(days=180),expected))
                dates=sorted(str(row[0]) for row in cur.fetchall())
                if len(dates)<61 or dates[-1]!=expected:
                    raise ValueError(f'BaoStock库未覆盖所需收盘日{expected}（最新{dates[-1] if dates else "无"}）')
                # Compare to the exchange calendar so an absent full-market day cannot vanish silently.
                day=date.fromisoformat(dates[0]); required=[]
                while day<=date.fromisoformat(expected):
                    if is_trading_day(day): required.append(day.isoformat())
                    day+=timedelta(days=1)
                if required!=dates: raise ValueError('BaoStock历史窗口缺少完整交易日')
                cur.execute("""SELECT b.security_id,s.code_name FROM finance.daily_bars b
                    JOIN finance.securities s USING (security_id)
                    WHERE b.dataset_id=%s AND b.dataset_version=ANY(%s) AND b.adjust_type='raw'
                    AND b.source_id=%s AND b.trade_date=ANY(%s::date[])
                    AND b.security_id ~ '^(000|001|002|003|600|601|603|605)[0-9]{3}\\.(SH|SZ)$'
                    AND b.pct_chg>0 AND b.trade_status=1 AND b.is_st=0 AND s.code_name IS NOT NULL
                    AND upper(s.code_name) NOT LIKE '%%ST%%' AND s.code_name NOT LIKE '%%退%%'
                    GROUP BY b.security_id,s.code_name HAVING count(*)=3 ORDER BY b.security_id""",
                    (DATASET,versions,SOURCE,dates[-3:]))
                candidates=cur.fetchall()
                cur.execute("""SELECT security_id,trade_date,close,preclose,volume,pct_chg,turnover_pct,
                    trade_status,is_st,row_hash,source_response_hash FROM finance.daily_bars
                    WHERE dataset_id=%s AND dataset_version=ANY(%s) AND adjust_type='raw' AND source_id=%s
                    AND security_id=ANY(%s) AND trade_date BETWEEN %s AND %s ORDER BY security_id,trade_date""",
                    (DATASET,versions,SOURCE,[x[0] for x in candidates],dates[0],expected))
                columns=[c.name for c in cur.description]; grouped={}
                for row in cur.fetchall():
                    b=dict(zip(columns,row)); b['trade_date']=str(b['trade_date'])
                    grouped.setdefault(b['security_id'],[]).append(b)
        verified=[]; excluded=[]
        for sid,name in candidates:
            try: verified.append(_score(sid.split('.')[0],name,grouped.get(sid,[]),expected,dates))
            except (ValueError,TypeError,ZeroDivisionError) as exc: excluded.append({'security_id':sid,'reason':str(exc)})
        verified.sort(key=lambda s:(-s['score'],-s['days'],s['code']))
        scope_versions=scope['dataset_versions']
        result=dict(ok=True,date=expected,source='https://www.baostock.com',daily_source='https://www.baostock.com',
            dataset_id=DATASET,dataset_version=scope['active_version'],dataset_versions=scope_versions,
            latest_trade_date=scope['latest_trade_date'],data_quality_status=scope['data_quality_status'],
            reader_receipt_hash=scope['reader_receipt_hash'],source_id=SOURCE,rule_version=RULE_VERSION,
            total=len(candidates),verified=len(verified),top=verified[:10],excluded=excluded,
            price_basis='official_preclose_aligned_to_latest_close',lookup_window=[dates[0],dates[-1]],
            generated_at=datetime.now(CST).isoformat(),report_trade_date=trade_date,
            verified_candidates=verified)
    except (CurrentBaoStockReadError,psycopg.Error,OSError,ValueError) as exc:
        result=dict(ok=False,reason=str(exc),source_id=SOURCE,dataset_id=DATASET,
            date=expected,rule_version=RULE_VERSION,report_trade_date=trade_date,generated_at=datetime.now(CST).isoformat())
    result['receipt_hash']='sha256:'+hashlib.sha256(json.dumps(result,ensure_ascii=False,sort_keys=True,
        separators=(',',':')).encode()).hexdigest()
    receipts=WORKSPACE/'reports/preopen/source_receipts'
    receipts.mkdir(parents=True,exist_ok=True)
    path=receipts/f'baostock_streak_{trade_date}_asof_{expected}.json'
    temp=path.with_suffix('.tmp');temp.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');temp.replace(path)
    result['receipt_path']=str(path)
    return result

def render_section(result):
    if not result.get('ok'):
        return f"> ⚠️ BaoStock 连涨筛选不可用：{result.get('reason','未取到数据')}。"
    rows=[f"> 候选与历史指标均来自 [BaoStock](https://www.baostock.com) PostgreSQL 历史库；"
        f"截至 {result['date']} 收盘，沪深主板、非ST、连续上涨≥3个交易日，"
        f"初选 {result['total']} 只，历史指标通过 {result['verified']} 只。"
        f"数据集 `{result['dataset_id']}`；实际版本 `{', '.join(result['dataset_versions'])}`；"
        f"最新行情 `{result['latest_trade_date']}`；质量状态 `{result['data_quality_status']}`；规则 `{result['rule_version']}`。",
        '', '| 排名 | 股票 | 收盘价 | 连涨 | 复合涨幅 | 累计换手 | 动量/18.75 | 持续/22.5 | 换手/15 | 风险/18.75 | 日线/25 | 总分/100 | 依据 |',
        '|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|']
    for i,s in enumerate(result['top'],1):
        c=s['score_components']
        rows.append(f"| {i} | {s['name']}（{s['code']}） | {s['price']:.2f} | {s['days']}天 | {s['gain']:.2f}% | "
            f"{s['turnover']:.2f}% | {c['momentum']} | {c['persistence']} | {c['liquidity']} | {c['risk']} | "
            f"{c['technical']} | {s['score']:.1f} | {s['logic']} |")
    if not result['top']: rows.append('当前完整数据下没有通过规则的候选。')
    rows.extend(['', '> 连涨以官方 pctChg>0 且正常交易判断；平盘、下跌或停牌中断。累计涨幅为每日涨幅的复合值，累计换手为实际每日换手之和。',
        '> 均线和60日高点以官方前收盘价对齐至末日价格，消除除权除息的机械跳变；这不是官方前复权报价。均量比采用原始成交股数，拆股等事件可能影响该指标。',
        '> 权重沿用既有规则，未经回测校准；排名表示观察优先级，不代表上涨概率。此为当前观察报告，证券名称为最近观测值，不宣称历史PIT回测已验收。',
        f"> Current reader 回执哈希：`{result['reader_receipt_hash']}`；来源回执：`{result['receipt_path']}`；内容哈希：`{result['receipt_hash']}`。"])
    return '\n'.join(rows)
