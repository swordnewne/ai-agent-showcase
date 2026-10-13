#!/usr/bin/env python3
"""彩排驱动：伪造 08:05 盘前场次跑 preopen_report.run() 全链路（2026-10-13）。

仅用于彩排：monkeypatch get_session 到盘前，其余全真实（新闻采集、节点1 DeepSeek、
P0.8 逐只评审、decision JSON、报告与桌面镜像）。
"""
from datetime import datetime

import market_session
import preopen_report

_fake_now = datetime(2026, 10, 13, 8, 5, tzinfo=market_session.CST)
preopen_report.get_session = lambda: market_session.get_session(_fake_now)

if __name__ == '__main__':
    result = preopen_report.run(slot='rehearsal')
    print('REHEARSAL_RESULT:', result)
