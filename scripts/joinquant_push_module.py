# 聚宽策略外部推送模块
# 用法：在策略代码末尾添加，或在 initialize() 中调用 init_push_api()
# 将交易记录、持仓快照、每日资产自动推送到外部API

import json
import urllib2
import time

# ==================== 配置区域 ====================
# 外部API接收地址（部署后替换为实际地址）
PUSH_API_URL = "https://your-api-endpoint.com/api/v1/joinquant/push"
# 可选：添加密钥验证
PUSH_API_KEY = "your-secret-key"
# 是否启用推送
PUSH_ENABLED = True
# 推送失败重试次数
PUSH_MAX_RETRY = 2

def _push_data(payload, context):
    """内部推送函数，带重试和错误处理"""
    if not PUSH_ENABLED:
        return
    
    # 添加时间戳和策略标识
    payload['_timestamp'] = str(context.current_dt) if hasattr(context, 'current_dt') else str(time.time())
    payload['_version'] = VERSION_FINGERPRINT if 'VERSION_FINGERPRINT' in globals() else 'unknown'
    payload['_backtest_id'] = getattr(context, 'backtest_id', 'unknown')
    
    headers = {
        'Content-Type': 'application/json',
    }
    if PUSH_API_KEY:
        headers['X-API-Key'] = PUSH_API_KEY
    
    data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    
    for attempt in range(PUSH_MAX_RETRY + 1):
        try:
            req = urllib2.Request(PUSH_API_URL, data=data, headers=headers)
            response = urllib2.urlopen(req, timeout=10)
            result = response.read()
            log.info('推送成功: %s' % payload.get('type', 'unknown'))
            return True
        except Exception as e:
            if attempt < PUSH_MAX_RETRY:
                log.warn('推送失败(重试%s/%s): %s' % (attempt + 1, PUSH_MAX_RETRY, e))
                time.sleep(2)
            else:
                log.warn('推送最终失败: %s' % e)
    return False


def push_trade(trade, context):
    """
    推送单笔交易记录
    
    Args:
        trade: 聚宽Trade对象（来自get_trades()）
    """
    payload = {
        'type': 'trade',
        'date': str(context.current_dt.date()) if hasattr(context, 'current_dt') else '',
        'time': str(context.current_dt.time()) if hasattr(context, 'current_dt') else '',
        'symbol': trade.security,
        'name': format_stock(trade.security) if 'format_stock' in globals() else trade.security,
        'side': 'buy' if trade.amount > 0 else 'sell',
        'quantity': abs(trade.amount),
        'price': float(trade.price),
        'value': abs(trade.amount) * float(trade.price),
        'trade_id': trade.order_id,
    }
    _push_data(payload, context)


def push_portfolio_snapshot(context):
    """
    推送当前持仓快照
    """
    positions = []
    for stock, pos in context.portfolio.positions.items():
        positions.append({
            'symbol': stock,
            'quantity': int(pos.total_amount),
            'sellable': int(pos.sellable_amount or pos.total_amount),
            'avg_cost': float(pos.avg_cost) if pos.avg_cost else 0,
            'current_value': float(pos.value),
            'pnl_pct': float((pos.price - pos.avg_cost) / pos.avg_cost * 100) if pos.avg_cost and pos.avg_cost > 0 else 0,
        })
    
    total = context.portfolio.total_value
    start = context.portfolio.starting_cash
    
    payload = {
        'type': 'portfolio',
        'date': str(context.current_dt.date()) if hasattr(context, 'current_dt') else '',
        'total_value': float(total),
        'starting_cash': float(start),
        'available_cash': float(context.portfolio.available_cash),
        'total_return_pct': float((total - start) / start * 100) if start > 0 else 0,
        'positions': positions,
        'position_count': len(positions),
    }
    _push_data(payload, context)


def push_daily_summary(context):
    """
    推送每日资产汇总（替代record_daily的print版）
    """
    total = context.portfolio.total_value
    start = context.portfolio.starting_cash
    pnl = (total - start) / start if start > 0 else 0
    holding = sum(p.value for p in context.portfolio.positions.values())
    ratio = holding / total if total > 0 else 0
    
    # 获取状态（如果策略中定义了这些全局变量）
    market_state = 'unknown'
    position_factor = 1.0
    cut_stage = 0
    trailing_triggered = False
    
    if 'g' in globals() and hasattr(g, 'market_state'):
        state_names = {0: '观察', 1: '试仓', 2: '上升趋势', 3: '退出', 4: '冷却'}
        market_state = state_names.get(g.market_state, 'unknown')
        position_factor = getattr(g, 'position_factor', 1.0)
        cut_stage = getattr(g, 'cut_stage', 0)
        trailing_triggered = getattr(g, 'trailing_stop_triggered', False)
    
    payload = {
        'type': 'daily_summary',
        'date': str(context.current_dt.date()) if hasattr(context, 'current_dt') else '',
        'total_value': float(total),
        'total_return_pct': float(pnl * 100),
        'holding_ratio_pct': float(ratio * 100),
        'market_state': market_state,
        'position_factor': float(position_factor),
        'cut_stage': int(cut_stage),
        'trailing_stop_triggered': bool(trailing_triggered),
    }
    _push_data(payload, context)


# ==================== 集成到现有策略的修改点 ====================
# 在原有策略代码中做以下替换：

# 1. 替换 record_fills() 函数体：
# 原代码：
#   def record_fills(context):
#       try: trades = get_trades()
#       except: return
#       for tid, t in trades.items():
#           if tid in g.seen_trade_ids: continue
#           g.seen_trade_ids.add(tid)
#           log.info('成交回报...')
#
# 新代码：
#   def record_fills(context):
#       try: trades = get_trades()
#       except: return
#       for tid, t in trades.items():
#           if tid in g.seen_trade_ids: continue
#           g.seen_trade_ids.add(tid)
#           log.info('成交回报 %s: 数量=%s 成交价=%.3f' % (format_stock(t.security), t.amount, t.price))
#           push_trade(t, context)  # <-- 新增推送

# 2. 替换 record_daily() 函数体：
#   def record_daily(context):
#       ... 原计算逻辑 ...
#       log.info('[%s] 总资产=...' % (...))
#       push_daily_summary(context)  # <-- 新增推送
#       push_portfolio_snapshot(context)  # <-- 可选：同时推送持仓

# 3. 在 initialize() 中可选添加：
#   run_daily(lambda ctx: push_portfolio_snapshot(ctx), time='14:55')  # 每日收盘前推送持仓

# ==================== 本地接收API（FastAPI示例） ====================
# 需要在服务器部署以下API来接收数据：
"""
# server.py (FastAPI)
from fastapi import FastAPI, Header
from pydantic import BaseModel
from typing import List, Optional
import json
from datetime import datetime

app = FastAPI()

class TradeRecord(BaseModel):
    type: str
    date: str
    time: str
    symbol: str
    name: str
    side: str
    quantity: int
    price: float
    value: float
    trade_id: str

class PositionItem(BaseModel):
    symbol: str
    quantity: int
    sellable: int
    avg_cost: float
    current_value: float
    pnl_pct: float

class PortfolioSnapshot(BaseModel):
    type: str
    date: str
    total_value: float
    starting_cash: float
    available_cash: float
    total_return_pct: float
    positions: List[PositionItem]
    position_count: int

class DailySummary(BaseModel):
    type: str
    date: str
    total_value: float
    total_return_pct: float
    holding_ratio_pct: float
    market_state: str
    position_factor: float
    cut_stage: int
    trailing_stop_triggered: bool

@app.post("/api/v1/joinquant/push")
async def receive_push(data: dict, x_api_key: Optional[str] = Header(None)):
    # 验证密钥
    if x_api_key != "your-secret-key":
        return {"status": "error", "msg": "invalid key"}
    
    # 保存到文件或数据库
    data_type = data.get('type', 'unknown')
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    with open(f"/data/joinquant/{data_type}_{timestamp}.json", "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    
    return {"status": "ok", "type": data_type}

# 运行: uvicorn server:app --host 0.0.0.0 --port 8000
"""
