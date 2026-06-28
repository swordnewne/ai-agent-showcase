# -*- coding: utf-8 -*-
"""
纯文本报告生成器
核心能力：
1. 个股分析报告（纯文本/表格）
2. 持仓组合日报
3. 大盘环境摘要
4. 无UI依赖，输出微信可读的文本

Python 3.6+ 兼容
"""

import logging
from datetime import date
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)


class ReportGenerator:
    """纯文本报告生成器"""
    
    # 微信消息长度限制（安全值）
    MAX_MSG_LENGTH = 2000
    
    def __init__(self):
        pass
    
    # ------------------------------------------------------------------
    # 个股分析报告
    # ------------------------------------------------------------------
    
    def generate_stock_report(self,
                              stock_code: str,
                              stock_name: str,
                              analysis_result: Dict,
                              position_advice: Dict,
                              market_context: Optional[Dict] = None) -> str:
        """
        生成个股分析报告
        
        输出格式：微信友好的纯文本，带emoji和表格
        """
        score = analysis_result.get("total_score", 0)
        grade = analysis_result.get("grade", "N/A")
        scores = analysis_result.get("scores", {})
        advice = analysis_result.get("advice", {})
        details = analysis_result.get("details", {})
        
        # 评级emoji
        grade_emoji = {
            "A+": "🔥", "A": "✅", "B+": "👍",
            "B": "➖", "C": "⚠️", "D": "❌"
        }.get(grade, "❓")
        
        lines = [
            "═" * 32,
            "{} {} {} 分析报告".format(grade_emoji, stock_name, stock_code),
            "═" * 32,
            "",
            "📊 综合评分: {}/100  评级: {}".format(score, grade),
            "",
            "📈 分项评分:",
            "  趋势    {:>3}/{}  {}".format(
                scores.get("trend", 0), 20,
                self._bar(scores.get("trend", 0), 20)),
            "  估值    {:>3}/{}  {}".format(
                scores.get("valuation", 0), 15,
                self._bar(scores.get("valuation", 0), 15)),
            "  资金    {:>3}/{}  {}".format(
                scores.get("fund_flow", 0), 15,
                self._bar(scores.get("fund_flow", 0), 15)),
            "  基本面  {:>3}/{}  {}".format(
                scores.get("fundamental", 0), 20,
                self._bar(scores.get("fundamental", 0), 20)),
            "  技术面  {:>3}/{}  {}".format(
                scores.get("technical", 0), 20,
                self._bar(scores.get("technical", 0), 20)),
            "  情绪    {:>3}/{}  {}".format(
                scores.get("sentiment", 0), 10,
                self._bar(scores.get("sentiment", 0), 10)),
            "",
            "💡 操作建议: {}".format(advice.get("action", "hold").upper()),
            "   {}".format(advice.get("summary", "")),
        ]
        
        if advice.get("risk_note"):
            lines.append("   ⚠️ {}".format(advice["risk_note"]))
        
        # 仓位建议
        lines.extend([
            "",
            "💰 仓位建议:",
            "   凯利理论: {}%".format(position_advice.get("kelly_pct", 0)),
            "   调整后: {}%".format(position_advice.get("adjusted_pct", 0)),
        ])
        
        if position_advice.get("suggested_shares", 0) > 0:
            lines.append("   建议买入: {}股 (约{}元)".format(
                position_advice["suggested_shares"],
                position_advice["suggested_amount"]
            ))
        else:
            lines.append("   建议观望")
        
        # 大盘环境
        if market_context:
            lines.extend([
                "",
                "🌤️ 大盘环境:",
                "   情绪分: {}/100".format(market_context.get("sentiment_score", 50)),
            ])
            risk_tags = market_context.get("risk_tags", [])
            if risk_tags:
                lines.append("   风险: {}".format(", ".join(risk_tags)))
        
        lines.append("")
        lines.append("─" * 32)
        
        return "\n".join(lines)
    
    # ------------------------------------------------------------------
    # 持仓组合日报
    # ------------------------------------------------------------------
    
    def generate_portfolio_report(self,
                                  snapshot: Dict,
                                  signals: Optional[List[Dict]] = None) -> str:
        """
        生成持仓组合日报
        
        包含：总资产、盈亏、持仓明细、今日信号
        """
        lines = [
            "═" * 36,
            "📊 持仓日报  {}",
            "═" * 36,
            "",
            "💰 资产概览:",
            "   总权益:  {:>12,.2f}".format(snapshot.get("total_equity", 0)),
            "   现金:    {:>12,.2f}".format(snapshot.get("total_cash", 0)),
            "   总市值:  {:>12,.2f}".format(snapshot.get("total_market_value", 0)),
            "",
            "📈 盈亏:",
            "   已实现:  {:>+12,.2f}".format(snapshot.get("realized_pnl", 0)),
            "   浮盈浮亏: {:>+12,.2f}".format(snapshot.get("unrealized_pnl", 0)),
            "",
        ]
        
        # 持仓明细
        positions = snapshot.get("positions", [])
        if positions:
            lines.append("📋 持仓明细:")
            lines.append("  代码      数量      成本      市值      盈亏")
            lines.append("  " + "-" * 42)
            
            for pos in positions:
                symbol = pos.get("symbol", "")
                qty = pos.get("quantity", 0)
                cost = pos.get("cost_basis", 0)
                mv = pos.get("market_value", 0)
                pnl = pos.get("unrealized_pnl", 0)
                pnl_pct = pos.get("unrealized_pnl_pct", 0)
                
                lines.append(
                    "  {:<8} {:>8} {:>10,.0f} {:>10,.0f} {:>+8,.0f}({:+.1f}%)"
                    .format(symbol, qty, cost, mv, pnl, pnl_pct)
                )
        
        # 今日信号
        if signals:
            lines.extend([
                "",
                "🔔 今日信号:",
            ])
            for sig in signals:
                emoji = {"buy": "🔥", "sell": "❌", "add": "👍",
                         "reduce": "⚠️", "hold": "➖"}.get(sig.get("signal_type"), "❓")
                lines.append(
                    "   {} {} {}  置信度:{}/100  仓位:{:.1f}%"
                    .format(
                        emoji,
                        sig.get("stock_name", ""),
                        sig.get("stock_code", ""),
                        sig.get("confidence", 0),
                        sig.get("kelly_fraction", 0) * 100
                    )
                )
        
        lines.extend(["", "─" * 36])
        
        return "\n".join(lines)
    
    # ------------------------------------------------------------------
    # 大盘环境报告
    # ------------------------------------------------------------------
    
    def generate_market_report(self,
                               context: Dict,
                               stock_pool_signals: Optional[List[Dict]] = None) -> str:
        """生成大盘环境简报"""
        sentiment = context.get("sentiment_score", 50)
        risk_tags = context.get("risk_tags", [])
        position_cap = context.get("position_cap", "")
        summary = context.get("summary", "")
        
        # 情绪emoji
        if sentiment >= 70:
            mood = "😊 积极"
        elif sentiment >= 50:
            mood = "😐 中性"
        elif sentiment >= 30:
            mood = "😰 谨慎"
        else:
            mood = "😱 恐慌"
        
        lines = [
            "═" * 32,
            "🌤️ 大盘环境  {}".format(context.get("trade_date", "")),
            "═" * 32,
            "",
            "情绪: {} ({}/100)".format(mood, sentiment),
        ]
        
        if risk_tags:
            lines.append("风险: {}".format(", ".join(risk_tags)))
        
        if position_cap:
            lines.append("仓位提示: {}".format(position_cap))
        
        if summary:
            lines.extend(["", "摘要:", summary])
        
        # 关注清单信号
        if stock_pool_signals:
            lines.extend(["", "📋 今日关注:"])
            for sig in sorted(stock_pool_signals,
                               key=lambda x: x.get("confidence", 0),
                               reverse=True)[:5]:
                lines.append(
                    "   {} {} {}/100"
                    .format(
                        sig.get("stock_code", ""),
                        sig.get("stock_name", ""),
                        sig.get("confidence", 0)
                    )
                )
        
        lines.extend(["", "─" * 32])
        
        return "\n".join(lines)
    
    # ------------------------------------------------------------------
    # 信号准确率报告
    # ------------------------------------------------------------------
    
    def generate_accuracy_report(self, stats: Dict) -> str:
        """生成AI信号准确率统计报告"""
        lines = [
            "═" * 32,
            "🎯 AI信号准确率统计 (近{}天)".format(stats.get("period_days", 90)),
            "═" * 32,
            "",
            "总信号: {}  已验证: {}".format(
                stats.get("total_signals", 0),
                stats.get("verified_signals", 0)
            ),
            "",
            "结果分布:",
            "   ✅ 止盈命中: {}".format(stats.get("hit_tp_count", 0)),
            "   ❌ 止损命中: {}".format(stats.get("hit_sl_count", 0)),
            "   ➖ 到期未触发: {}".format(stats.get("expired_count", 0)),
            "",
            "胜率: {}%".format(stats.get("win_rate", 0)),
            "平均收益: {}%".format(stats.get("avg_return", 0)),
            "",
        ]
        
        by_type = stats.get("by_signal_type", {})
        if by_type:
            lines.append("按信号类型:")
            for stype, data in sorted(by_type.items()):
                lines.append(
                    "   {}: 胜率{}% 均收益{}% (验证{}/{})"
                    .format(
                        stype.upper(),
                        data.get("win_rate", 0),
                        data.get("avg_return", 0),
                        data.get("verified", 0),
                        data.get("total", 0)
                    )
                )
        
        # 信任权重
        win_rate = stats.get("win_rate", 0)
        if win_rate >= 60:
            trust = "🔥 高信任 (权重1.0)"
        elif win_rate >= 50:
            trust = "✅ 正常 (权重0.8)"
        elif win_rate >= 40:
            trust = "⚠️ 偏低 (权重0.6)"
        else:
            trust = "❌ 告警 (权重0.4，建议检查策略)"
        
        lines.extend(["", "AI信任等级: {}".format(trust), "", "─" * 32])
        
        return "\n".join(lines)
    
    # ------------------------------------------------------------------
    # 辅助
    # ------------------------------------------------------------------
    
    def _bar(self, value: float, max_val: float, width: int = 10) -> str:
        """生成ASCII进度条"""
        if max_val <= 0:
            return ""
        filled = int(value / max_val * width)
        filled = max(0, min(filled, width))
        return "█" * filled + "░" * (width - filled)
    
    def truncate_for_wechat(self, text: str) -> str:
        """截断以适应微信消息长度"""
        if len(text) <= self.MAX_MSG_LENGTH:
            return text
        
        truncated = text[:self.MAX_MSG_LENGTH - 20]
        # 找到最后一个完整行
        last_newline = truncated.rfind("\n")
        if last_newline > self.MAX_MSG_LENGTH * 0.8:
            truncated = truncated[:last_newline]
        
        return truncated + "\n... (内容过长，已截断)"


# 全局实例
_generator = None


def get_generator() -> ReportGenerator:
    """获取全局报告生成器"""
    global _generator
    if _generator is None:
        _generator = ReportGenerator()
    return _generator
