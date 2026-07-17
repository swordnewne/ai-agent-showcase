# -*- coding: utf-8 -*-
"""
持仓事件溯源与快照回放模块
核心能力：
1. 事件表记录所有交易（buy/sell/cash/dividend/split）
2. FIFO/均价 成本回放
3. 实时持仓快照（市值、盈亏、仓位占比）
4. 防超卖校验

Python 3.6+ 兼容
"""

import logging
import json
import os
from collections import defaultdict
from datetime import date
from typing import Dict, List, Optional, Tuple, Any

logger = logging.getLogger(__name__)

EPS = 1e-8
VALID_SIDES = {"buy", "sell"}
VALID_CASH_DIRS = {"in", "out"}


class PortfolioTracker:
    """
    持仓追踪器
    
    使用事件溯源（Event Sourcing）：
    - 不直接存状态，只存事件
    - 每天回放事件计算当前持仓
    - 支持任意历史日期复盘
    """
    
    def __init__(self, db_path: Optional[str] = None, account_id: str = "default"):
        if db_path is None:
            db_path = os.path.join(
                os.path.dirname(__file__), "..", "..", "..", "data", "finance.db"
            )
            db_path = os.path.abspath(db_path)
        self.db_path = db_path
        self.account_id = account_id
        self._ensure_table()
    
    def _get_conn(self):
        import sqlite3
        return sqlite3.connect(self.db_path)
    
    def _ensure_table(self):
        """确保表存在"""
        schema_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "data", "db_schema.sql"
        )
        schema_path = os.path.abspath(schema_path)
        if os.path.exists(schema_path):
            with open(schema_path, "r", encoding="utf-8") as f:
                sql = f.read()
            conn = self._get_conn()
            try:
                conn.executescript(sql)
                conn.commit()
            finally:
                conn.close()
    
    # ------------------------------------------------------------------
    # 事件写入
    # ------------------------------------------------------------------
    
    def record_trade(self, symbol: str, trade_date: date,
                     side: str, quantity: float, price: float,
                     fee: float = 0.0, tax: float = 0.0,
                     note: str = "") -> int:
        """
        记录交易事件
        
        Args:
            symbol: 股票代码，如 600519.SH
            trade_date: 交易日期
            side: 'buy' 或 'sell'
            quantity: 股数
            price: 成交价
            fee: 佣金
            tax: 印花税
            note: 备注
        
        Returns:
            事件ID
        
        Raises:
            ValueError: 参数非法
            PortfolioOversellError: 卖出数量超过可售数量
        """
        side = side.strip().lower()
        if side not in VALID_SIDES:
            raise ValueError("side must be buy or sell")
        if quantity <= 0 or price <= 0:
            raise ValueError("quantity and price must be > 0")
        if fee < 0 or tax < 0:
            raise ValueError("fee and tax must be >= 0")
        
        symbol = symbol.strip().upper()
        
        # 防超卖校验
        if side == "sell":
            available = self.get_available_quantity(symbol, trade_date)
            if available + EPS < quantity:
                raise PortfolioOversellError(
                    symbol=symbol, trade_date=trade_date,
                    requested=quantity, available=available
                )
        
        conn = self._get_conn()
        try:
            cursor = conn.execute(
                """INSERT INTO portfolio_events 
                   (event_type, account_id, symbol, event_date, side,
                    quantity, price, fee, tax, note)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                ("trade", self.account_id, symbol, trade_date.isoformat(),
                 side, quantity, price, fee, tax, note or None)
            )
            conn.commit()
            event_id = cursor.lastrowid
            logger.info("记录交易: %s %s %s  %.0f股 @ %.2f",
                        trade_date.isoformat(), side, symbol, quantity, price)
            return event_id
        finally:
            conn.close()
    
    def record_cash(self, event_date: date, direction: str,
                    amount: float, note: str = "") -> int:
        """记录资金出入"""
        direction = direction.strip().lower()
        if direction not in VALID_CASH_DIRS:
            raise ValueError("direction must be in or out")
        if amount <= 0:
            raise ValueError("amount must be > 0")
        
        conn = self._get_conn()
        try:
            cursor = conn.execute(
                """INSERT INTO portfolio_events
                   (event_type, account_id, event_date, direction, amount, note)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                ("cash", self.account_id, event_date.isoformat(),
                 direction, amount, note or None)
            )
            conn.commit()
            return cursor.lastrowid
        finally:
            conn.close()
    
    def record_dividend(self, symbol: str, effective_date: date,
                        dividend_per_share: float, note: str = "") -> int:
        """记录分红"""
        conn = self._get_conn()
        try:
            cursor = conn.execute(
                """INSERT INTO portfolio_events
                   (event_type, account_id, symbol, event_date,
                    amount, note)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                ("dividend", self.account_id, symbol,
                 effective_date.isoformat(), dividend_per_share, note or None)
            )
            conn.commit()
            return cursor.lastrowid
        finally:
            conn.close()
    
    def record_split(self, symbol: str, effective_date: date,
                     split_ratio: float, note: str = "") -> int:
        """
        记录送股/拆股
        
        Args:
            split_ratio: 拆股比例，如 1.5 表示 10送5（每10股变15股）
        """
        if split_ratio <= 0:
            raise ValueError("split_ratio must be > 0")
        
        conn = self._get_conn()
        try:
            cursor = conn.execute(
                """INSERT INTO portfolio_events
                   (event_type, account_id, symbol, event_date,
                    quantity, note)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                ("split", self.account_id, symbol,
                 effective_date.isoformat(), split_ratio, note or None)
            )
            conn.commit()
            return cursor.lastrowid
        finally:
            conn.close()
    
    # ------------------------------------------------------------------
    # 回放计算
    # ------------------------------------------------------------------
    
    def replay(self, as_of_date: Optional[date] = None,
               cost_method: str = "fifo") -> Dict[str, Any]:
        """
        回放计算持仓快照
        
        Args:
            as_of_date: 截止日期，默认今天
            cost_method: 'fifo' 或 'avg'
        
        Returns:
            持仓快照字典
        """
        as_of = as_of_date or date.today()
        
        # 拉取所有事件
        events = self._load_events(as_of)
        
        # 按日期排序
        events.sort(key=lambda e: (e["event_date"], e["id"]))
        
        # 回放状态
        cash_balance = 0.0
        fifo_lots = defaultdict(list)   # symbol -> [lot1, lot2, ...]
        avg_state = defaultdict(lambda: {"qty": 0.0, "total_cost": 0.0})
        realized_pnl = 0.0
        fee_total = 0.0
        tax_total = 0.0
        
        for evt in events:
            etype = evt["event_type"]
            
            if etype == "cash":
                amount = float(evt["amount"] or 0)
                if evt["direction"] == "in":
                    cash_balance += amount
                else:
                    cash_balance -= amount
                continue
            
            if etype == "trade":
                symbol = evt["symbol"]
                qty = float(evt["quantity"] or 0)
                price = float(evt["price"] or 0)
                fee = float(evt["fee"] or 0)
                tax = float(evt["tax"] or 0)
                side = evt["side"]
                
                gross = qty * price
                
                if side == "buy":
                    cash_balance -= (gross + fee + tax)
                    if cost_method == "fifo":
                        unit_cost = (gross + fee + tax) / qty if qty > 0 else 0
                        fifo_lots[symbol].append({
                            "open_date": evt["event_date"],
                            "remaining_qty": qty,
                            "unit_cost": unit_cost,
                        })
                    else:  # avg
                        state = avg_state[symbol]
                        state["qty"] += qty
                        state["total_cost"] += (gross + fee + tax)
                
                else:  # sell
                    cash_balance += (gross - fee - tax)
                    proceeds_net = gross - fee - tax
                    
                    if cost_method == "fifo":
                        cost_basis = self._consume_fifo(fifo_lots[symbol], qty)
                    else:
                        cost_basis = self._consume_avg(avg_state[symbol], qty)
                    
                    realized_pnl += (proceeds_net - cost_basis)
                    fee_total += fee
                    tax_total += tax
                
                continue
            
            if etype == "dividend":
                symbol = evt["symbol"]
                per_share = float(evt["amount"] or 0)
                if per_share > 0:
                    qty_held = self._held_quantity(symbol, fifo_lots, avg_state, cost_method)
                    if qty_held > EPS:
                        cash_balance += qty_held * per_share
                continue
            
            if etype == "split":
                symbol = evt["symbol"]
                ratio = float(evt["quantity"] or 1.0)
                if abs(ratio - 1.0) > EPS:
                    if cost_method == "fifo":
                        for lot in fifo_lots[symbol]:
                            lot["remaining_qty"] *= ratio
                            lot["unit_cost"] /= ratio
                    else:
                        state = avg_state[symbol]
                        state["qty"] *= ratio
                continue
        
        # 构建持仓明细
        positions = []
        total_market_value = 0.0
        total_cost = 0.0
        
        symbols = set(fifo_lots.keys()) if cost_method == "fifo" else set(avg_state.keys())
        
        for symbol in sorted(symbols):
            if cost_method == "fifo":
                active_lots = [lot for lot in fifo_lots[symbol]
                               if lot["remaining_qty"] > EPS]
                qty = sum(lot["remaining_qty"] for lot in active_lots)
                cost = sum(lot["remaining_qty"] * lot["unit_cost"] for lot in active_lots)
            else:
                state = avg_state[symbol]
                qty = state["qty"]
                cost = state["total_cost"] if state["qty"] > EPS else 0
            
            if qty <= EPS:
                continue
            
            # TODO: 接入实时行情获取当前价
            current_price = self._get_current_price(symbol)
            market_value = qty * current_price if current_price else cost
            
            positions.append({
                "symbol": symbol,
                "quantity": round(qty, 2),
                "avg_cost": round(cost / qty, 3) if qty > EPS else 0,
                "cost_basis": round(cost, 2),
                "market_value": round(market_value, 2),
                "unrealized_pnl": round(market_value - cost, 2),
                "unrealized_pnl_pct": round((market_value - cost) / cost * 100, 2) if cost > EPS else 0,
            })
            
            total_market_value += market_value
            total_cost += cost
        
        total_equity = cash_balance + total_market_value
        
        snapshot = {
            "as_of": as_of.isoformat(),
            "account_id": self.account_id,
            "cost_method": cost_method,
            "total_cash": round(cash_balance, 2),
            "total_market_value": round(total_market_value, 2),
            "total_equity": round(total_equity, 2),
            "realized_pnl": round(realized_pnl, 2),
            "unrealized_pnl": round(total_market_value - total_cost, 2),
            "fee_total": round(fee_total, 2),
            "tax_total": round(tax_total, 2),
            "position_count": len(positions),
            "positions": positions,
        }
        
        # 保存快照
        self._save_snapshot(snapshot)
        
        return snapshot
    
    def _load_events(self, as_of: date) -> List[Dict]:
        """加载截止日期前的事件"""
        conn = self._get_conn()
        try:
            cursor = conn.execute(
                """SELECT id, event_type, symbol, event_date, side,
                          quantity, price, fee, tax, direction, amount
                   FROM portfolio_events
                   WHERE account_id = ? AND event_date <= ?
                   ORDER BY event_date, id""",
                (self.account_id, as_of.isoformat())
            )
            cols = [desc[0] for desc in cursor.description]
            return [dict(zip(cols, row)) for row in cursor.fetchall()]
        finally:
            conn.close()
    
    def _consume_fifo(self, lots: List[Dict], quantity: float) -> float:
        """FIFO消耗持仓，返回成本基数"""
        remaining = quantity
        cost_basis = 0.0
        
        while remaining > EPS and lots:
            lot = lots[0]
            lot_qty = lot["remaining_qty"]
            
            if lot_qty <= remaining:
                cost_basis += lot_qty * lot["unit_cost"]
                remaining -= lot_qty
                lots.pop(0)
            else:
                cost_basis += remaining * lot["unit_cost"]
                lot["remaining_qty"] -= remaining
                remaining = 0
        
        return cost_basis
    
    def _consume_avg(self, state: Dict, quantity: float) -> float:
        """均价消耗持仓"""
        if state["qty"] <= EPS:
            return 0.0
        
        avg_cost = state["total_cost"] / state["qty"]
        cost_basis = quantity * avg_cost
        
        state["qty"] -= quantity
        state["total_cost"] -= cost_basis
        
        if state["qty"] <= EPS:
            state["qty"] = 0.0
            state["total_cost"] = 0.0
        
        return cost_basis
    
    def _held_quantity(self, symbol: str, fifo_lots, avg_state, cost_method: str) -> float:
        """计算当前持有数量"""
        if cost_method == "fifo":
            return sum(lot["remaining_qty"] for lot in fifo_lots[symbol]
                       if lot["remaining_qty"] > EPS)
        else:
            return avg_state[symbol]["qty"]
    
    def _get_current_price(self, symbol: str) -> Optional[float]:
        """获取当前价格（接入新浪API）"""
        try:
            from ..financial.data_provider import DataProvider
            dp = DataProvider()
            spot = dp.get_stock_spot_sina(symbol)
            if spot and spot.get("close"):
                return float(spot["close"])
        except Exception as e:
            logger.debug("获取 %s 当前价格失败: %s", symbol, e)
        return None
    
    def _save_snapshot(self, snapshot: Dict) -> None:
        """保存快照到数据库"""
        conn = self._get_conn()
        try:
            conn.execute(
                """INSERT OR REPLACE INTO sig_portfolio_snapshots
                   (snapshot_date, account_id, total_equity, total_cash,
                    total_market_value, unrealized_pnl, realized_pnl,
                    fee_total, tax_total, positions_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    snapshot["as_of"],
                    self.account_id,
                    snapshot["total_equity"],
                    snapshot["total_cash"],
                    snapshot["total_market_value"],
                    snapshot["unrealized_pnl"],
                    snapshot["realized_pnl"],
                    snapshot["fee_total"],
                    snapshot["tax_total"],
                    json.dumps(snapshot["positions"], ensure_ascii=False)
                )
            )
            conn.commit()
        finally:
            conn.close()
    
    # ------------------------------------------------------------------
    # 查询接口
    # ------------------------------------------------------------------
    
    def get_available_quantity(self, symbol: str, as_of: Optional[date] = None) -> float:
        """
        获取指定股票可售数量（防超卖用）
        """
        as_of = as_of or date.today()
        events = self._load_events(as_of)
        
        qty = 0.0
        for evt in events:
            if evt["symbol"] != symbol:
                continue
            
            etype = evt["event_type"]
            
            if etype == "trade":
                q = float(evt["quantity"] or 0)
                if evt["side"] == "buy":
                    qty += q
                else:
                    qty -= q
            
            elif etype == "split":
                ratio = float(evt["quantity"] or 1.0)
                qty *= ratio
        
        return max(0.0, qty)
    
    def get_latest_snapshot(self) -> Optional[Dict]:
        """获取最新持仓快照"""
        conn = self._get_conn()
        try:
            cursor = conn.execute(
                """SELECT snapshot_date, total_equity, total_cash,
                          total_market_value, unrealized_pnl, realized_pnl,
                          fee_total, tax_total, positions_json
                   FROM sig_portfolio_snapshots
                   WHERE account_id = ?
                   ORDER BY snapshot_date DESC LIMIT 1""",
                (self.account_id,)
            )
            row = cursor.fetchone()
            if not row:
                return None
            
            return {
                "snapshot_date": row[0],
                "total_equity": row[1],
                "total_cash": row[2],
                "total_market_value": row[3],
                "unrealized_pnl": row[4],
                "realized_pnl": row[5],
                "fee_total": row[6],
                "tax_total": row[7],
                "positions": json.loads(row[8]) if row[8] else []
            }
        finally:
            conn.close()
    
    def get_trade_history(self, symbol: Optional[str] = None,
                          limit: int = 50) -> List[Dict]:
        """获取交易历史"""
        conn = self._get_conn()
        try:
            sql = ("SELECT id, symbol, event_date, side, quantity, price, "
                   "fee, tax, note FROM portfolio_events "
                   "WHERE account_id = ? AND event_type = 'trade'")
            params = [self.account_id]
            
            if symbol:
                sql += " AND symbol = ?"
                params.append(symbol)
            
            sql += " ORDER BY event_date DESC, id DESC LIMIT ?"
            params.append(limit)
            
            cursor = conn.execute(sql, params)
            cols = [desc[0] for desc in cursor.description]
            return [dict(zip(cols, row)) for row in cursor.fetchall()]
        finally:
            conn.close()


class PortfolioOversellError(Exception):
    """超卖异常"""
    
    def __init__(self, symbol: str, trade_date: date,
                 requested: float, available: float):
        self.symbol = symbol
        self.trade_date = trade_date
        self.requested = requested
        self.available = available
        super().__init__(
            "超卖: {} on {}: 请求卖出 {:.0f}股, 可用 {:.0f}股".format(
                symbol, trade_date.isoformat(), requested, available
            )
        )
