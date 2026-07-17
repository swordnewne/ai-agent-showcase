# -*- coding: utf-8 -*-
"""
交易信号追踪模块
核心能力：
1. AI分析生成结构化信号（buy/sell/hold/add/reduce）
2. 自动追踪信号结果（N天后验证是否hit_tp/hit_sl/expired）
3. 统计AI信号准确率，动态调整信任权重

Python 3.6+ 兼容
"""

import logging
import json
import os
import uuid
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)


class SignalTracker:
    """
    信号追踪器
    
    流程：
    1. AI分析 → 生成 DecisionSignal（含目标价/止损价）
    2. 保存到数据库
    3. N天后自动验证（对比实际走势）
    4. 统计准确率
    """
    
    DEFAULT_VERIFY_DAYS = 5
    
    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            db_path = os.path.join(
                os.path.dirname(__file__), "..", "..", "..", "data", "finance.db"
            )
            db_path = os.path.abspath(db_path)
        self.db_path = db_path
        self._ensure_table()
    
    def _get_conn(self):
        import sqlite3
        return sqlite3.connect(self.db_path)
    
    def _ensure_table(self):
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
    # 信号生成
    # ------------------------------------------------------------------
    
    def create_signal(self,
                      stock_code: str,
                      stock_name: str,
                      signal_type: str,
                      confidence: int,
                      reason: str,
                      score_total: int = 0,
                      score_breakdown: Optional[Dict] = None,
                      kelly_fraction: float = 0.0,
                      target_price: Optional[float] = None,
                      stop_loss: Optional[float] = None,
                      suggested_shares: int = 0,
                      market_context: str = "",
                      verify_days: int = 5) -> str:
        """
        创建交易信号
        
        Args:
            stock_code: 股票代码
            stock_name: 股票名称
            signal_type: 'buy', 'sell', 'hold', 'add', 'reduce'
            confidence: 0-100 置信度
            reason: AI分析理由
            score_total: 综合评分
            score_breakdown: 分项评分字典
            kelly_fraction: 凯利公式建议仓位比例
            target_price: 目标价
            stop_loss: 止损价
            suggested_shares: 建议股数
            market_context: 当日大盘上下文
            verify_days: N天后验证
        
        Returns:
            signal_id (UUID字符串)
        """
        signal_id = "sig_{}_{}".format(
            datetime.now().strftime("%Y%m%d_%H%M%S"),
            uuid.uuid4().hex[:8]
        )
        
        conn = self._get_conn()
        try:
            conn.execute(
                """INSERT INTO sig_decision_signals
                   (signal_id, stock_code, stock_name, signal_type, confidence,
                    reason, score_total, score_breakdown, kelly_fraction,
                    target_price, stop_loss, suggested_shares,
                    market_context, verified_days)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    signal_id, stock_code, stock_name, signal_type, confidence,
                    reason, score_total,
                    json.dumps(score_breakdown, ensure_ascii=False) if score_breakdown else None,
                    kelly_fraction,
                    target_price, stop_loss, suggested_shares,
                    market_context, verify_days
                )
            )
            conn.commit()
            logger.info("创建信号: %s %s %s (置信度:%d)",
                        signal_id, signal_type, stock_code, confidence)
            return signal_id
        finally:
            conn.close()
    
    # ------------------------------------------------------------------
    # 信号验证
    # ------------------------------------------------------------------
    
    def verify_signal(self, signal_id: str,
                      future_data: Optional[Dict] = None) -> Optional[Dict]:
        """
        验证单个信号的结果
        
        Args:
            signal_id: 信号ID
            future_data: 未来N天行情数据（high, low, close序列）
                        为None时自动从数据库/接口获取
        
        Returns:
            验证结果字典
        """
        signal = self._get_signal(signal_id)
        if not signal:
            logger.warning("信号不存在: %s", signal_id)
            return None
        
        if signal.get("outcome"):
            logger.info("信号已验证: %s = %s", signal_id, signal["outcome"])
            return signal
        
        # 获取验证所需的行情数据
        if future_data is None:
            future_data = self._fetch_future_data(
                signal["stock_code"],
                signal["created_at"],
                signal.get("verified_days", self.DEFAULT_VERIFY_DAYS)
            )
        
        if not future_data:
            logger.warning("无法获取验证数据: %s", signal_id)
            return None
        
        # 执行验证
        result = self._do_verify(signal, future_data)
        
        # 保存结果
        self._save_verification(signal_id, result)
        
        return result
    
    def verify_pending_signals(self, batch_size: int = 50) -> List[Dict]:
        """
        批量验证所有待验证信号
        
        由定时任务调用（如每天收盘后）
        """
        pending = self._get_pending_signals(batch_size)
        results = []
        
        for signal in pending:
            try:
                result = self.verify_signal(signal["signal_id"])
                if result:
                    results.append(result)
            except Exception as e:
                logger.error("验证信号失败 %s: %s", signal["signal_id"], e)
        
        logger.info("批量验证完成: %d/%d", len(results), len(pending))
        return results
    
    def _do_verify(self, signal: Dict, future_data: Dict) -> Dict:
        """执行验证逻辑"""
        signal_type = signal["signal_type"]
        target = signal.get("target_price")
        stop = signal.get("stop_loss")
        entry_price = self._infer_entry_price(signal)
        
        highs = future_data.get("high", [])
        lows = future_data.get("low", [])
        closes = future_data.get("close", [])
        
        if not highs or not lows or not closes:
            return {
                "signal_id": signal["signal_id"],
                "outcome": "expired",
                "outcome_return": 0.0,
                "reason": "无行情数据"
            }
        
        # 验证逻辑
        if signal_type in ("buy", "add"):
            # 买入信号：检查是否触发止盈或止损
            if target and max(highs) >= target:
                ret = (target - entry_price) / entry_price if entry_price else 0
                return {
                    "signal_id": signal["signal_id"],
                    "outcome": "hit_tp",
                    "outcome_return": round(ret * 100, 2),
                    "reason": "触及目标价 {:.2f}".format(target)
                }
            
            if stop and min(lows) <= stop:
                ret = (stop - entry_price) / entry_price if entry_price else 0
                return {
                    "signal_id": signal["signal_id"],
                    "outcome": "hit_sl",
                    "outcome_return": round(ret * 100, 2),
                    "reason": "触及止损价 {:.2f}".format(stop)
                }
        
        elif signal_type in ("sell", "reduce"):
            # 卖出信号：检查是否按预期下跌
            if target and min(lows) <= target:
                ret = (entry_price - target) / entry_price if entry_price else 0
                return {
                    "signal_id": signal["signal_id"],
                    "outcome": "hit_tp",
                    "outcome_return": round(ret * 100, 2),
                    "reason": "触及目标价（下跌）{:.2f}".format(target)
                }
            
            if stop and max(highs) >= stop:
                ret = (entry_price - stop) / entry_price if entry_price else 0
                return {
                    "signal_id": signal["signal_id"],
                    "outcome": "hit_sl",
                    "outcome_return": round(ret * 100, 2),
                    "reason": "触及止损价（上涨）{:.2f}".format(stop)
                }
        
        # 未触发，按到期处理
        final_price = closes[-1] if closes else entry_price
        ret = (final_price - entry_price) / entry_price if entry_price else 0
        
        return {
            "signal_id": signal["signal_id"],
            "outcome": "expired",
            "outcome_return": round(ret * 100, 2),
            "reason": "到期未触发，最终收益 {:.2f}%".format(ret * 100)
        }
    
    def _infer_entry_price(self, signal: Dict) -> Optional[float]:
        """推断入场价格"""
        # 如果有目标价和止损价，取中点作为估算入场价
        target = signal.get("target_price")
        stop = signal.get("stop_loss")
        
        if target and stop:
            # 买入：止损在下方，目标在上方
            if target > stop:
                return stop + (target - stop) * 0.3  # 约30%位置
            else:
                return target + (stop - target) * 0.3
        
        return target or stop or None
    
    def _fetch_future_data(self, stock_code: str,
                           created_at: str,
                           days: int) -> Optional[Dict]:
        """
        获取未来N天行情数据
        
        TODO: 接入akshare或本地数据库
        """
        # 占位实现
        logger.info("获取未来数据: %s from %s +%dd", stock_code, created_at, days)
        return None
    
    def _save_verification(self, signal_id: str, result: Dict) -> None:
        """保存验证结果"""
        conn = self._get_conn()
        try:
            conn.execute(
                """UPDATE sig_decision_signals
                   SET outcome = ?, outcome_return = ?, verified_at = ?
                   WHERE signal_id = ?""",
                (
                    result["outcome"],
                    result["outcome_return"],
                    datetime.now().isoformat(),
                    signal_id
                )
            )
            conn.commit()
            logger.info("信号验证完成: %s = %s (收益 %.2f%%)",
                        signal_id, result["outcome"], result["outcome_return"])
        finally:
            conn.close()
    
    # ------------------------------------------------------------------
    # 查询接口
    # ------------------------------------------------------------------
    
    def _get_signal(self, signal_id: str) -> Optional[Dict]:
        """获取单个信号"""
        conn = self._get_conn()
        try:
            cursor = conn.execute(
                """SELECT signal_id, stock_code, stock_name, signal_type,
                          confidence, reason, score_total, score_breakdown,
                          kelly_fraction, target_price, stop_loss,
                          suggested_shares, market_context, created_at,
                          outcome, outcome_return, verified_at, verified_days
                   FROM sig_decision_signals WHERE signal_id = ?""",
                (signal_id,)
            )
            row = cursor.fetchone()
            if not row:
                return None
            
            cols = [desc[0] for desc in cursor.description]
            result = dict(zip(cols, row))
            if result.get("score_breakdown"):
                try:
                    result["score_breakdown"] = json.loads(result["score_breakdown"])
                except Exception:
                    pass
            return result
        finally:
            conn.close()
    
    def _get_pending_signals(self, limit: int = 50) -> List[Dict]:
        """获取待验证信号"""
        # 创建时间 + verified_days <= 今天
        cutoff = (date.today() - timedelta(days=1)).isoformat()
        
        conn = self._get_conn()
        try:
            cursor = conn.execute(
                """SELECT signal_id, stock_code, stock_name, signal_type,
                          target_price, stop_loss, created_at, verified_days
                   FROM sig_decision_signals
                   WHERE outcome IS NULL
                     AND date(created_at, '+' || verified_days || ' days') <= date('now')
                   ORDER BY created_at LIMIT ?""",
                (limit,)
            )
            cols = [desc[0] for desc in cursor.description]
            return [dict(zip(cols, row)) for row in cursor.fetchall()]
        finally:
            conn.close()
    
    def get_recent_signals(self, stock_code: Optional[str] = None,
                           days: int = 30, limit: int = 50) -> List[Dict]:
        """获取近期信号"""
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        
        conn = self._get_conn()
        try:
            sql = ("SELECT signal_id, stock_code, stock_name, signal_type, "
                   "confidence, score_total, kelly_fraction, target_price, "
                   "stop_loss, outcome, outcome_return, created_at "
                   "FROM sig_decision_signals WHERE created_at >= ?")
            params = [cutoff]
            
            if stock_code:
                sql += " AND stock_code = ?"
                params.append(stock_code)
            
            sql += " ORDER BY created_at DESC LIMIT ?"
            params.append(limit)
            
            cursor = conn.execute(sql, params)
            cols = [desc[0] for desc in cursor.description]
            return [dict(zip(cols, row)) for row in cursor.fetchall()]
        finally:
            conn.close()
    
    # ------------------------------------------------------------------
    # 统计：AI信号准确率
    # ------------------------------------------------------------------
    
    def get_accuracy_stats(self, days: int = 90) -> Dict[str, Any]:
        """
        统计AI信号准确率
        
        Returns:
            {
                "total_signals": 总信号数,
                "verified_signals": 已验证数,
                "hit_tp_count": 止盈命中数,
                "hit_sl_count": 止损命中数,
                "expired_count": 到期未触发数,
                "win_rate": 胜率（止盈/已验证）,
                "avg_return": 平均收益率,
                "by_signal_type": 按信号类型分组统计
            }
        """
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        
        conn = self._get_conn()
        try:
            # 总体统计
            cursor = conn.execute(
                """SELECT 
                    COUNT(*) as total,
                    COUNT(outcome) as verified,
                    SUM(CASE WHEN outcome = 'hit_tp' THEN 1 ELSE 0 END) as tp_count,
                    SUM(CASE WHEN outcome = 'hit_sl' THEN 1 ELSE 0 END) as sl_count,
                    SUM(CASE WHEN outcome = 'expired' THEN 1 ELSE 0 END) as expired_count,
                    AVG(outcome_return) as avg_return
                   FROM sig_decision_signals
                   WHERE created_at >= ?""",
                (cutoff,)
            )
            total, verified, tp_count, sl_count, expired_count, avg_ret = cursor.fetchone()
            
            # 按信号类型分组
            cursor = conn.execute(
                """SELECT signal_type,
                    COUNT(*) as count,
                    COUNT(outcome) as verified,
                    SUM(CASE WHEN outcome = 'hit_tp' THEN 1 ELSE 0 END) as tp,
                    AVG(outcome_return) as avg_ret
                   FROM sig_decision_signals
                   WHERE created_at >= ?
                   GROUP BY signal_type""",
                (cutoff,)
            )
            
            by_type = {}
            for row in cursor.fetchall():
                stype, count, ver, tp, avg_r = row
                by_type[stype] = {
                    "total": count,
                    "verified": ver,
                    "win_count": tp,
                    "win_rate": round(tp / ver * 100, 1) if ver else 0,
                    "avg_return": round(avg_r, 2) if avg_r else 0
                }
            
            win_rate = round(tp_count / verified * 100, 1) if verified else 0
            
            return {
                "period_days": days,
                "total_signals": total or 0,
                "verified_signals": verified or 0,
                "hit_tp_count": tp_count or 0,
                "hit_sl_count": sl_count or 0,
                "expired_count": expired_count or 0,
                "win_rate": win_rate,
                "avg_return": round(avg_ret, 2) if avg_ret else 0,
                "by_signal_type": by_type
            }
        finally:
            conn.close()
    
    def get_ai_trust_weight(self, days: int = 30) -> float:
        """
        获取AI当前信任权重（0.0-1.0）
        
        基于近期信号准确率动态调整：
        - 胜率>60% → 权重1.0
        - 胜率50-60% → 权重0.8
        - 胜率40-50% → 权重0.6
        - 胜率<40% → 权重0.4（需要告警）
        """
        stats = self.get_accuracy_stats(days)
        win_rate = stats.get("win_rate", 0)
        
        if win_rate >= 60:
            return 1.0
        elif win_rate >= 50:
            return 0.8
        elif win_rate >= 40:
            return 0.6
        else:
            return 0.4
