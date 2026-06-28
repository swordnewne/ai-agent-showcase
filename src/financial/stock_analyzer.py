# -*- coding: utf-8 -*-
"""
个股综合分析评分器（100分制）
核心能力：
1. 六维度加权评分（趋势/估值/资金/基本面/技术/情绪）
2. 纯数学计算，无UI依赖
3. 输出结构化评分 + 分项明细

Python 3.6+ 兼容
"""

import logging
from typing import Dict, Optional, Any

logger = logging.getLogger(__name__)


class StockAnalyzer:
    """
    个股100分综合评分器
    
    六维度评分：
    - 趋势得分 (20分): MA多头排列、价格位置
    - 估值得分 (15分): PE/PB分位、相对行业
    - 资金得分 (15分): 成交量、主力资金流向
    - 基本面得分 (20分): 营收/利润增速、ROE
    - 技术面得分 (20分): MACD/RSI/乖离率
    - 情绪得分 (10分): 新闻舆情、市场热度
    """
    
    # 维度权重
    WEIGHTS = {
        "trend": 20,
        "valuation": 15,
        "fund_flow": 15,
        "fundamental": 20,
        "technical": 20,
        "sentiment": 10,
    }
    
    def __init__(self):
        self.total_weight = sum(self.WEIGHTS.values())
    
    def analyze(self,
                stock_code: str,
                market_data: Dict[str, Any],
                fundamental_data: Optional[Dict] = None,
                news_sentiment: Optional[Dict] = None) -> Dict[str, Any]:
        """
        执行综合分析
        
        Args:
            stock_code: 股票代码
            market_data: 行情数据（含价格、MA、MACD、RSI、成交量等）
            fundamental_data: 基本面数据（PE/PB/ROE/营收增速等）
            news_sentiment: 新闻情绪数据
        
        Returns:
            结构化评分结果
        """
        scores = {}
        details = {}
        
        # 1. 趋势评分
        scores["trend"], details["trend"] = self._score_trend(market_data)
        
        # 2. 估值评分
        scores["valuation"], details["valuation"] = self._score_valuation(
            market_data, fundamental_data
        )
        
        # 3. 资金评分
        scores["fund_flow"], details["fund_flow"] = self._score_fund_flow(market_data)
        
        # 4. 基本面评分
        scores["fundamental"], details["fundamental"] = self._score_fundamental(
            fundamental_data
        )
        
        # 5. 技术面评分
        scores["technical"], details["technical"] = self._score_technical(market_data)
        
        # 6. 情绪评分
        scores["sentiment"], details["sentiment"] = self._score_sentiment(
            news_sentiment, market_data
        )
        
        # 加权总分
        total = sum(
            scores.get(k, 0) * (self.WEIGHTS[k] / self.total_weight)
            for k in self.WEIGHTS
        )
        
        # 评级
        grade = self._grade(total)
        
        # 操作建议
        advice = self._generate_advice(total, scores, market_data)
        
        return {
            "stock_code": stock_code,
            "total_score": round(total, 1),
            "grade": grade,
            "max_possible": self.total_weight,
            "scores": {k: round(v, 1) for k, v in scores.items()},
            "details": details,
            "advice": advice,
        }
    
    # ------------------------------------------------------------------
    # 分项评分
    # ------------------------------------------------------------------
    
    def _score_trend(self, data: Dict) -> tuple:
        """
        趋势评分（0-20）
        
        评分标准：
        - MA5>MA10>MA20 多头排列: +8
        - 价格在MA5上方: +4
        - 价格在MA20上方: +4
        - 均线发散（MA5-MA20差值扩大）: +4
        """
        score = 0
        reasons = []
        
        ma5 = data.get("ma5")
        ma10 = data.get("ma10")
        ma20 = data.get("ma20")
        close = data.get("close")
        
        if ma5 and ma10 and ma20:
            if ma5 > ma10 > ma20:
                score += 8
                reasons.append("MA多头排列")
            elif ma5 > ma10:
                score += 4
                reasons.append("短期均线金叉")
            else:
                reasons.append("均线空头排列")
        
        if close and ma5:
            if close > ma5:
                score += 4
                reasons.append("价格在MA5上方")
            else:
                reasons.append("价格跌破MA5")
        
        if close and ma20:
            if close > ma20:
                score += 4
                reasons.append("价格在MA20上方")
            else:
                reasons.append("价格跌破MA20")
        
        if ma5 and ma20 and ma5 > ma20:
            spread_pct = (ma5 - ma20) / ma20 * 100 if ma20 > 0 else 0
            if spread_pct > 2:
                score += 4
                reasons.append("均线发散({:.1f}%)".format(spread_pct))
            else:
                reasons.append("均线收敛")
        
        return min(score, 20), reasons
    
    def _score_valuation(self, market_data: Dict,
                         fundamental: Optional[Dict]) -> tuple:
        """
        估值评分（0-15）
        
        评分标准：
        - PE分位<30%（低估）: +5
        - PE分位30-70%（合理）: +3
        - PB分位<30%: +5
        - 相对行业折价: +5
        """
        score = 0
        reasons = []
        
        if not fundamental:
            reasons.append("无基本面数据")
            return 7, reasons  # 默认中等
        
        pe = fundamental.get("pe_ttm")
        pe_percentile = fundamental.get("pe_percentile")
        pb = fundamental.get("pb")
        pb_percentile = fundamental.get("pb_percentile")
        
        # PE评分
        if pe_percentile is not None:
            if pe_percentile < 0.3:
                score += 5
                reasons.append("PE分位{:.1%}（低估）".format(pe_percentile))
            elif pe_percentile < 0.7:
                score += 3
                reasons.append("PE分位{:.1%}（合理）".format(pe_percentile))
            else:
                reasons.append("PE分位{:.1%}（高估）".format(pe_percentile))
        elif pe and pe > 0:
            if pe < 20:
                score += 4
                reasons.append("PE{:.1f}（偏低）".format(pe))
            elif pe < 40:
                score += 2
                reasons.append("PE{:.1f}（中等）".format(pe))
            else:
                reasons.append("PE{:.1f}（偏高）".format(pe))
        
        # PB评分
        if pb_percentile is not None:
            if pb_percentile < 0.3:
                score += 5
                reasons.append("PB分位{:.1%}（低估）".format(pb_percentile))
            elif pb_percentile < 0.7:
                score += 3
                reasons.append("PB分位{:.1%}（合理）".format(pb_percentile))
        elif pb and pb > 0:
            if pb < 2:
                score += 3
                reasons.append("PB{:.1f}（偏低）".format(pb))
            elif pb < 4:
                score += 1
                reasons.append("PB{:.1f}（中等）".format(pb))
        
        # 相对行业
        pe_vs_industry = fundamental.get("pe_vs_industry")
        if pe_vs_industry and pe_vs_industry < 0:
            score += 5
            reasons.append("相对行业折价{:.1%}".format(abs(pe_vs_industry)))
        elif pe_vs_industry and pe_vs_industry > 0.3:
            reasons.append("相对行业溢价{:.1%}".format(pe_vs_industry))
        
        return min(score, 15), reasons
    
    def _score_fund_flow(self, data: Dict) -> tuple:
        """
        资金评分（0-15）
        
        评分标准：
        - 近5日主力资金净流入: +5
        - 成交量较20日均量放大: +5
        - 换手率适中（2-8%）: +5
        """
        score = 0
        reasons = []
        
        # 主力资金
        main_flow_5d = data.get("main_flow_5d", 0)
        if main_flow_5d > 0:
            score += 5
            reasons.append("5日主力净流入")
        elif main_flow_5d < 0:
            reasons.append("5日主力净流出")
        
        # 成交量
        vol = data.get("volume", 0)
        vol_ma20 = data.get("vol_ma20", 0)
        if vol and vol_ma20 and vol_ma20 > 0:
            vol_ratio = vol / vol_ma20
            if vol_ratio > 1.5:
                score += 5
                reasons.append("放量{:.1f}倍".format(vol_ratio))
            elif vol_ratio > 1.0:
                score += 3
                reasons.append("温和放量")
            else:
                reasons.append("缩量")
        
        # 换手率
        turnover = data.get("turnover", 0)
        if 0.02 <= turnover <= 0.08:
            score += 5
            reasons.append("换手率适中({:.1%})".format(turnover))
        elif turnover > 0.15:
            reasons.append("换手过高({:.1%})".format(turnover))
        elif turnover > 0:
            reasons.append("换手偏低({:.1%})".format(turnover))
        
        return min(score, 15), reasons
    
    def _score_fundamental(self, data: Optional[Dict]) -> tuple:
        """
        基本面评分（0-20）
        
        评分标准：
        - 营收增速>20%: +5
        - 净利润增速>20%: +5
        - ROE>15%: +5
        - 毛利率稳定或提升: +5
        """
        score = 0
        reasons = []
        
        if not data:
            reasons.append("无基本面数据")
            return 10, reasons  # 默认中等
        
        # 营收增速
        revenue_growth = data.get("revenue_growth_yoy")
        if revenue_growth and revenue_growth > 0.2:
            score += 5
            reasons.append("营收增速{:.1%}".format(revenue_growth))
        elif revenue_growth and revenue_growth > 0:
            score += 3
            reasons.append("营收正增长{:.1%}".format(revenue_growth))
        elif revenue_growth:
            reasons.append("营收下滑{:.1%}".format(revenue_growth))
        
        # 净利润增速
        profit_growth = data.get("profit_growth_yoy")
        if profit_growth and profit_growth > 0.2:
            score += 5
            reasons.append("利润增速{:.1%}".format(profit_growth))
        elif profit_growth and profit_growth > 0:
            score += 3
            reasons.append("利润正增长{:.1%}".format(profit_growth))
        elif profit_growth:
            reasons.append("利润下滑{:.1%}".format(profit_growth))
        
        # ROE
        roe = data.get("roe")
        if roe and roe > 0.15:
            score += 5
            reasons.append("ROE{:.1%}（优秀）".format(roe))
        elif roe and roe > 0.1:
            score += 3
            reasons.append("ROE{:.1%}（良好）".format(roe))
        elif roe:
            reasons.append("ROE{:.1%}（偏低）".format(roe))
        
        # 毛利率
        gross_margin = data.get("gross_margin")
        margin_change = data.get("gross_margin_change_yoy")
        if gross_margin and gross_margin > 0.3:
            score += 3
            reasons.append("毛利率{:.1%}".format(gross_margin))
        if margin_change and margin_change > 0:
            score += 2
            reasons.append("毛利率提升{:.1%}".format(margin_change))
        elif margin_change and margin_change < 0:
            reasons.append("毛利率下滑{:.1%}".format(margin_change))
        
        return min(score, 20), reasons
    
    def _score_technical(self, data: Dict) -> tuple:
        """
        技术面评分（0-20）
        
        评分标准：
        - MACD金叉或红柱扩大: +5
        - RSI在40-70区间（健康）: +5
        - 乖离率合理（<5%）: +5
        - 未出现顶背离: +5
        """
        score = 0
        reasons = []
        
        close = data.get("close", 0)
        ma5 = data.get("ma5", 0)
        
        # MACD
        macd = data.get("macd")
        macd_signal = data.get("macd_signal")
        if macd and macd_signal:
            if macd > macd_signal and macd > 0:
                score += 5
                reasons.append("MACD金叉且红柱")
            elif macd > macd_signal:
                score += 3
                reasons.append("MACD金叉")
            else:
                reasons.append("MACD死叉或绿柱")
        
        # RSI
        rsi = data.get("rsi14")
        if rsi is not None:
            if 40 <= rsi <= 70:
                score += 5
                reasons.append("RSI健康({:.1f})".format(rsi))
            elif rsi < 30:
                score += 3
                reasons.append("RSI超卖({:.1f})".format(rsi))
            elif rsi > 80:
                reasons.append("RSI超买({:.1f})".format(rsi))
            else:
                reasons.append("RSI{:.1f}".format(rsi))
        
        # 乖离率
        if close and ma5 and ma5 > 0:
            bias = abs(close - ma5) / ma5 * 100
            if bias < 3:
                score += 5
                reasons.append("乖离率低({:.1f}%)".format(bias))
            elif bias < 5:
                score += 3
                reasons.append("乖离率合理({:.1f}%)".format(bias))
            else:
                reasons.append("乖离率偏高({:.1f}%)".format(bias))
        
        # 背离检测（简化版）
        price_high = data.get("high_20d")
        macd_high = data.get("macd_high_20d")
        if price_high and macd_high:
            # 价格新高但MACD未新高 = 顶背离
            if close >= price_high and macd < macd_high:
                reasons.append("⚠️ 顶背离信号")
            else:
                score += 5
                reasons.append("无背离")
        
        return min(score, 20), reasons
    
    def _score_sentiment(self, news: Optional[Dict],
                         market_data: Dict) -> tuple:
        """
        情绪评分（0-10）
        
        评分标准：
        - 新闻情绪正面: +4
        - 舆情热度适中: +3
        - 无重大负面: +3
        """
        score = 0
        reasons = []
        
        if not news:
            reasons.append("无新闻数据")
            return 5, reasons  # 默认中性
        
        sentiment = news.get("sentiment", "neutral")
        if sentiment == "positive":
            score += 4
            reasons.append("新闻情绪正面")
        elif sentiment == "negative":
            reasons.append("新闻情绪负面")
        else:
            score += 2
            reasons.append("新闻情绪中性")
        
        # 舆情热度
        heat = news.get("heat_score", 50)
        if 30 <= heat <= 70:
            score += 3
            reasons.append("舆情热度适中")
        elif heat > 70:
            score += 2
            reasons.append("舆情热度高（注意追高风险）")
        else:
            reasons.append("舆情热度低")
        
        # 负面新闻检测
        negative_count = news.get("negative_count", 0)
        if negative_count == 0:
            score += 3
            reasons.append("无重大负面")
        elif negative_count <= 2:
            score += 1
            reasons.append("少量负面({}条)".format(negative_count))
        else:
            reasons.append("负面较多({}条)".format(negative_count))
        
        return min(score, 10), reasons
    
    # ------------------------------------------------------------------
    # 评级 & 建议
    # ------------------------------------------------------------------
    
    def _grade(self, total: float) -> str:
        """根据总分评级"""
        if total >= 85:
            return "A+"  # 强烈推荐
        elif total >= 75:
            return "A"   # 推荐
        elif total >= 65:
            return "B+"  # 谨慎推荐
        elif total >= 55:
            return "B"   # 中性
        elif total >= 45:
            return "C"   # 谨慎
        else:
            return "D"   # 回避
    
    def _generate_advice(self, total: float, scores: Dict,
                         market_data: Dict) -> Dict[str, str]:
        """生成操作建议（考虑分项得分）"""
        advice = {
            "action": "hold",
            "summary": "",
            "risk_note": ""
        }
        
        close = market_data.get("close", 0)
        ma20 = market_data.get("ma20", 0)
        
        # 判断基本面和技术面的强弱
        fundamental_score = scores.get("fundamental", 0)
        technical_score = scores.get("technical", 0)
        trend_score = scores.get("trend", 0)
        
        # 基本面强 + 趋势弱 = 好公司但时机不对
        strong_fundamental = fundamental_score >= 12  # 14分以上算强
        weak_trend = trend_score <= 4
        
        if total >= 80:
            advice["action"] = "buy"
            advice["summary"] = "综合评分优秀，趋势和基本面共振，建议关注买入机会"
        elif total >= 65:
            advice["action"] = "add"
            advice["summary"] = "评分良好，可考虑加仓或逢低布局"
        elif total >= 50:
            advice["action"] = "hold"
            advice["summary"] = "评分中性，建议观望或持有现有仓位"
        elif total >= 35:
            advice["action"] = "reduce"
            advice["summary"] = "评分偏弱，建议减仓控制风险"
        else:
            # 总分低但分项差异大时的精准建议
            advice["action"] = "sell"
            if strong_fundamental and weak_trend:
                advice["summary"] = "基本面优秀但趋势走弱，建议等待技术面企稳后再关注"
            else:
                advice["summary"] = "评分较差，趋势和基本面均不利，建议回避"
        
        # 风险提示
        risks = []
        if scores.get("technical", 20) < 8:
            risks.append("技术面走弱")
        if scores.get("sentiment", 10) < 4:
            risks.append("情绪偏空")
        if close and ma20 and close < ma20:
            risks.append("价格跌破MA20")
        
        if risks:
            advice["risk_note"] = "风险提示：" + "、".join(risks)
        
        return advice
    
    # ------------------------------------------------------------------
    # 便捷函数
    # ------------------------------------------------------------------
    
    def quick_score(self, market_data: Dict,
                    fundamental_data: Optional[Dict] = None) -> int:
        """快捷评分，只返回总分"""
        result = self.analyze(
            stock_code=market_data.get("code", ""),
            market_data=market_data,
            fundamental_data=fundamental_data
        )
        return int(result["total_score"])


# 全局实例
_analyzer = None


def get_analyzer() -> StockAnalyzer:
    """获取全局分析器实例"""
    global _analyzer
    if _analyzer is None:
        _analyzer = StockAnalyzer()
    return _analyzer


def quick_analyze(market_data: Dict,
                  fundamental_data: Optional[Dict] = None) -> Dict:
    """快捷分析入口"""
    code = market_data.get("code", "unknown")
    return get_analyzer().analyze(
        stock_code=code,
        market_data=market_data,
        fundamental_data=fundamental_data
    )
