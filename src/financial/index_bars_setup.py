#!/usr/bin/env python3
"""大盘指数日线表建表 + 首次全历史入库（BaoStock）。

- 表：finance.index_daily_bars（与股票 daily_bars 分离，指数无换手/股本语义）
- 首批：sh.000300 沪深300，2016-01-01 起（对齐股票历史基线起点）
- 纪律：原始响应存 artifacts + sha256 回执；入库后独立 SQL 读回核对行数与首尾日
"""
import hashlib
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import os

import baostock as bs
import psycopg

# 表已由超级用户建妥并对 runtime 用户授予 SELECT；本脚本负责抓取+写入，需写权限，
# 运行时通过环境变量 INDEX_DSN 传入可写连接（如 sudo -u postgres 本地 peer 连接）。
DSN = os.environ.get('INDEX_DSN') or Path('/root/.config/niko/finance-shadow.dsn').read_text().strip()
ARTIFACTS = Path(os.environ.get('INDEX_ART_DIR')
                 or '/root/.openclaw/workspace/finance_p0a1_artifacts/p1a3-backfill')
CST = timezone(timedelta(hours=8))
INDEX_ID = 'sh.000300'
SOURCE_ID = 'baostock-index-daily'
START = '2016-01-01'

DDL = """
CREATE TABLE IF NOT EXISTS finance.index_daily_bars (
    index_id      text        NOT NULL,
    trade_date    date        NOT NULL,
    close         numeric,
    preclose      numeric,
    pct_chg       numeric,
    volume        numeric,
    amount        numeric,
    trade_status  integer,
    source_id     text        NOT NULL,
    fetched_at    timestamptz NOT NULL,
    row_hash      text        NOT NULL,
    PRIMARY KEY (index_id, trade_date, source_id)
)
"""


def row_hash(index_id, r):
    return hashlib.sha256(json.dumps([index_id] + r).encode()).hexdigest()


def main():
    today = datetime.now(CST).date().isoformat()
    ARTIFACTS.mkdir(parents=True, exist_ok=True)

    lg = bs.login()
    if lg.error_code != '0':
        raise SystemExit(f'baostock login failed: {lg.error_msg}')
    rs = bs.query_history_k_data_plus(
        INDEX_ID, 'date,close,preclose,pctChg,volume,amount,tradestatus',
        start_date=START, end_date=today, frequency='d')
    rows = []
    while rs.error_code == '0' and rs.next():
        rows.append(rs.get_row_data())
    bs.logout()
    if not rows:
        raise SystemExit('baostock 返回 0 行')

    raw = {'index_id': INDEX_ID, 'source': 'baostock', 'fields': rs.fields,
           'range': [START, today], 'rows': rows,
           'fetched_at': datetime.now(CST).isoformat()}
    raw['receipt_hash'] = 'sha256:' + hashlib.sha256(
        json.dumps(rows, sort_keys=True).encode()).hexdigest()
    raw_path = ARTIFACTS / f'index_{INDEX_ID.replace(".", "_")}_raw_{today.replace("-", "")}.json'
    raw_path.write_text(json.dumps(raw, ensure_ascii=False, indent=1), encoding='utf-8')

    fetched_at = datetime.now(CST).isoformat()
    with psycopg.connect(DSN, connect_timeout=5) as conn:
        with conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO finance.index_daily_bars
                   (index_id, trade_date, close, preclose, pct_chg, volume, amount,
                    trade_status, source_id, fetched_at, row_hash)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (index_id, trade_date, source_id) DO NOTHING""",
                [(INDEX_ID, r[0],
                  float(r[1]) if r[1] else None, float(r[2]) if r[2] else None,
                  float(r[3]) if r[3] else None, float(r[4]) if r[4] else None,
                  float(r[5]) if r[5] else None, int(r[6]) if r[6] else None,
                  SOURCE_ID, fetched_at, row_hash(INDEX_ID, r)) for r in rows])
        conn.commit()
        # 独立读回
        with conn.cursor() as cur:
            cur.execute(
                """SELECT count(*), min(trade_date), max(trade_date),
                          count(*) FILTER (WHERE close IS NULL)
                     FROM finance.index_daily_bars
                    WHERE index_id=%s AND source_id=%s""", (INDEX_ID, SOURCE_ID))
            cnt, dmin, dmax, nulls = cur.fetchone()

    ok = cnt == len(rows) and str(dmin) == rows[0][0] and str(dmax) == rows[-1][0]
    print(json.dumps({
        'fetched_rows': len(rows), 'db_rows': cnt, 'first': str(dmin),
        'last': str(dmax), 'null_close_rows': nulls, 'readback_ok': ok,
        'raw_receipt': str(raw_path), 'raw_hash': raw['receipt_hash'][:24],
    }, ensure_ascii=False, indent=1))
    if not ok:
        raise SystemExit('读回不一致，不入账')


if __name__ == '__main__':
    main()
