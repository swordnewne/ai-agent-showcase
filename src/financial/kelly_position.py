# -*- coding: utf-8 -*-
"""
凯利公式仓位计算器
核心能力：
1. 基于胜率(p)和盈亏比(b)计算最优仓位
2. 半凯利/四分之一凯利保守策略
3. 结合大盘环境动态调整
4. 防All-in保护

Python 3.6+ 兼容
"""

import logging
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


class KellyPositionCalculator:
    """
    凯利公式仓位计算器
    
    凯利公式: f = (p * b - q) / b
    其中:
        p = 胜率 (如 0.6)
        q = 败率 = 1 - p
        b = 盈亏比 (平均盈利/平均亏损，如 2.0)
        f = 最优仓位比例 (如 0.5 = 50%)
    
    保守策略:
        - 半凯利: f * 0.5
        - 四分之一凯利: f * 0.25
    """
    
    # 默认参数（从用户历史偏好）
    DEFAULT_P = 0.6       # 胜率60%
    DEFAULT_B = 2.0       # 盈亏比2:1
    DEFAULT_KELLY_FRACTION = 0.5  # 半凯利
    
    # 风控约束
    MAX_SINGLE_POSITION = 0.20   # 单股最大20%
    MAX_TOTAL_POSITION = 0.90    # 总仓位最大90%（留10%现金）
    MIN_POSITION = 0.02          # 最小仓位2%（避免碎股）
    
    def __init__(self,
                 p: float = DEFAULT_P,
                 b: float = DEFAULT_B,
                 kelly_fraction: float = DEFAULT_KELLY_FRACTION):
        self.p = p
        self.b = b
        self.kelly_fraction = kelly_fraction
    
    def calculate(self,
                  total_equity: float,
                  stock_price: float,
                  p: Optional[float] = None,
                  b: Optional[float] = None,
                  market_context: Optional[Dict] = None,
                  stock_score: Optional[float] = None,
                  current_position_pct: float = 0.0) -> Dict[str, Any]:
        """
        计算建议仓位
        
        Args:
            total_equity: 总权益（现金+市值）
            stock_price: 当前股价
            p: 胜率（覆盖默认值）
            b: 盈亏比（覆盖默认值）
            market_context: 大盘上下文（用于环境调整）
            stock_score: 个股评分（0-100，用于质量调整）
            current_position_pct: 当前该股已占仓位比例
        
        Returns:
            {
                "kelly_pct": 凯利理论仓位比例,
                "adjusted_pct": 环境调整后的仓位比例,
                "suggested_amount": 建议金额,
                "suggested_shares": 建议股数,
                "max_shares": 最大可买股数（基于剩余仓位）,
                "remaining_capacity": 剩余仓位容量,
                "reasoning": 计算过程说明
            }
        """
        p = p if p is not None else self.p
        b = b if b is not None else self.b
        
        # 1. 计算凯利比例
        q = 1 - p
        if b <= 0:
            logger.warning("盈亏比必须>0")
            b = 1.0
        
        kelly_raw = (p * b - q) / b
        kelly_raw = max(0, kelly_raw)  # 不能为负
        
        # 2. 应用凯利分数（半凯利/四分之一凯利）
        kelly_pct = kelly_raw * self.kelly_fraction
        
        # 3. 环境调整
        adjusted_pct = self._apply_market_adjustment(kelly_pct, market_context)
        
        # 4. 质量调整（基于个股评分）
        adjusted_pct = self._apply_quality_adjustment(adjusted_pct, stock_score)
        
        # 5. 硬约束
        adjusted_pct = min(adjusted_pct, self.MAX_SINGLE_POSITION)
        adjusted_pct = max(adjusted_pct, 0)
        
        # 6. 计算金额和股数
        suggested_amount = total_equity * adjusted_pct
        suggested_shares = int(suggested_amount / stock_price) if stock_price > 0 else 0
        
        # 7. 总仓位约束
        max_total_amount = total_equity * self.MAX_TOTAL_POSITION
        current_total = total_equity * current_position_pct
        remaining_capacity = max_total_amount - current_total
        
        if suggested_amount > remaining_capacity:
            suggested_amount = remaining_capacity
            suggested_shares = int(suggested_amount / stock_price) if stock_price > 0 else 0
            adjusted_pct = suggested_amount / total_equity if total_equity > 0 else 0
        
        # 8. 最小仓位过滤
        if adjusted_pct > 0 and adjusted_pct < self.MIN_POSITION:
            logger.info("计算仓位%.2f%%低于最小阈值%.2f%%，建议观望", 
                        adjusted_pct * 100, self.MIN_POSITION * 100)
            suggested_shares = 0
            suggested_amount = 0
            adjusted_pct = 0
        
        # 9. 生成说明
        reasoning = self._build_reasoning(
            p, b, kelly_raw, kelly_pct, adjusted_pct,
            market_context, stock_score
        )
        
        return {
            "kelly_pct": round(kelly_raw * 100, 2),
            "conservative_pct": round(kelly_pct * 100, 2),
            "adjusted_pct": round(adjusted_pct * 100, 2),
            "suggested_amount": round(suggested_amount, 2),
            "suggested_shares": suggested_shares,
            "max_shares": int(remaining_capacity / stock_price) if stock_price > 0 else 0,
            "remaining_capacity": round(remaining_capacity, 2),
            "reasoning": reasoning,
        }
    
    def _apply_market_adjustment(self, base_pct: float,
                                  market_context: Optional[Dict]) -> float:
        """根据大盘环境调整仓位"""
        if not market_context:
            return base_pct
        
        adjusted = base_pct
        risk_tags = market_context.get("risk_tags", [])
        sentiment = market_context.get("sentiment_score", 50)
        
        # 高风险环境：砍半
        if "high_risk" in risk_tags:
            adjusted *= 0.5
            logger.info("高风险环境，仓位减半")
        
        # 退潮/观望：砍到70%
        elif "market_cooling" in risk_tags or "conservative" in risk_tags:
            adjusted *= 0.7
            logger.info("退潮/观望环境，仓位降至70%%")
        
        # 轻仓提示：直接限制上限
        if "low_position" in risk_tags:
            cap = market_context.get("position_cap", "")
            if "3成" in cap or "30%" in cap:
                adjusted = min(adjusted, 0.30)
            elif "半仓" in cap or "50%" in cap:
                adjusted = min(adjusted, 0.50)
        
        # 情绪分调整
        if sentiment < 30:
            adjusted *= 0.6
        elif sentiment < 45:
            adjusted *= 0.8
        elif sentiment > 75:
            adjusted *= 1.1  # 情绪好可以稍微积极
        
        return min(adjusted, self.MAX_SINGLE_POSITION)
    
    def _apply_quality_adjustment(self, base_pct: float,
                                   stock_score: Optional[float]) -> float:
        """基于个股评分调整仓位"""
        if stock_score is None:
            return base_pct
        
        if stock_score >= 85:
            return base_pct * 1.1  # 优质标的多给10%
        elif stock_score >= 70:
            return base_pct * 1.0  # 不变
        elif stock_score >= 55:
            return base_pct * 0.8  # 一般标的减20%
        else:
            return base_pct * 0.5  # 差标的减半
    
    def _build_reasoning(self, p: float, b: float,
                         kelly_raw: float, kelly_pct: float,
                         adjusted_pct: float,
                         market_context: Optional[Dict],
                         stock_score: Optional[float]) -> str:
        """构建计算过程说明"""
        lines = [
            "凯利公式计算过程:",
            "  胜率 p={:.0%}, 盈亏比 b={:.1f}".format(p, b),
            "  凯利理论仓位: f=(p*b-q)/b = {:.1%}".format(kelly_raw),
            "  {}凯利: {:.1%}".format(
                "半" if self.kelly_fraction == 0.5 else
                "四分之一" if self.kelly_fraction == 0.25 else
                "{:.0%}".format(self.kelly_fraction),
                kelly_pct
            ),
        ]
        
        if market_context:
            risk_tags = market_context.get("risk_tags", [])
            sentiment = market_context.get("sentiment_score", 50)
            if risk_tags:
                lines.append("  大盘风险标签: {}".format(", ".join(risk_tags)))
            lines.append("  情绪分: {}/100".format(sentiment))
        
        if stock_score:
            lines.append("  个股评分: {}/100".format(stock_score))
        
        lines.append("  最终建议仓位: {:.1%}".format(adjusted_pct))
        
        if adjusted_pct >= self.MAX_SINGLE_POSITION * 0.95:
            lines.append("  ⚠️ 触及单股仓位上限 {}".format(
                self.MAX_SINGLE_POSITION))
        
        return "\n".join(lines)
    
    # ------------------------------------------------------------------
    # 快捷计算
    # ------------------------------------------------------------------
    
    def quick_position(self,
                       total_equity: float,
                       stock_price: float,
                       stock_score: float = 60) -> Dict[str, Any]:
        """快捷计算（使用默认参数）"""
        return self.calculate(
            total_equity=total_equity,
            stock_price=stock_price,
            stock_score=stock_score
        )


# 全局实例
_kelly = None


def get_kelly_calculator(p: float = 0.6, b: float = 2.0,
                         fraction: float = 0.5) -> KellyPositionCalculator:
    """获取全局凯利计算器"""
    global _kelly
    if _kelly is None:
        _kelly = KellyPositionCalculator(p=p, b=b, kelly_fraction=fraction)
    return _kelly


def calculate_position(total_equity: float, stock_price: float,
                       stock_score: float = 60,
                       market_context: Optional[Dict] = None) -> Dict:
    """快捷计算仓位"""
    return get_kelly_calculator().calculate(
        total_equity=total_equity,
        stock_price=stock_price,
        stock_score=stock_score,
        market_context=market_context
    )
