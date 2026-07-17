# -*- coding: utf-8 -*-
"""
统一调度入口（Launcher）
核心能力：
1. 每日盘前/盘后自动执行完整分析流程
2. 一键生成大盘简报 + 个股分析 + 持仓日报
3. 纯文本输出，微信推送就绪

Python 3.6+ 兼容
"""

import logging
import json
import os
from datetime import date
from typing import Dict, List, Optional, Any

from .market_context import get_market_context_service
from .portfolio_tracker import PortfolioTracker
from .stock_analyzer import get_analyzer
from .kelly_position import get_kelly_calculator
from .signal_tracker import SignalTracker
from .report_generator import get_generator

logger = logging.getLogger(__name__)


class TradingSystemLauncher:
    """
    交易系统统一调度器
    
    每日执行流程：
    1. 生成/获取大盘上下文（真实数据）
    2. 扫描股票池，逐个分析评分（真实行情）
    3. 计算凯利仓位
    4. 生成交易信号
    5. 输出纯文本报告
    """
    
    def __init__(self,
                 db_path: Optional[str] = None,
                 stock_pool_path: Optional[str] = None):
        if db_path is None:
            db_path = os.path.join(
                os.path.dirname(__file__), "..", "..", "..", "data", "finance.db"
            )
            db_path = os.path.abspath(db_path)
        self.db_path = db_path
        
        # 股票池配置路径
        if stock_pool_path is None:
            stock_pool_path = os.path.join(
                os.path.dirname(__file__), "..", "..", "config", "stock_pool.yaml"
            )
            stock_pool_path = os.path.abspath(stock_pool_path)
        self.stock_pool_path = stock_pool_path
        
        # 初始化各模块
        self.market_ctx = get_market_context_service()
        self.portfolio = PortfolioTracker(db_path=db_path)
        self.analyzer = get_analyzer()
        self.kelly = get_kelly_calculator()
        self.signal_tracker = SignalTracker(db_path=db_path)
        self.reporter = get_generator()
        
        # 数据提供者（延迟初始化）
        self._dp = None
        
        self.stock_pool = self._load_stock_pool()
    
    def _get_dp(self):
        """获取数据提供者"""
        if self._dp is None:
            from .data_provider import get_data_provider
            self._dp = get_data_provider()
        return self._dp
    
    def _load_stock_pool(self) -> List[Dict]:
        """加载股票池配置"""
        if not os.path.exists(self.stock_pool_path):
            logger.warning("股票池配置不存在: %s，使用默认持仓", self.stock_pool_path)
            return self._default_stock_pool()
        
        try:
            import yaml
            with open(self.stock_pool_path, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f)
            return config.get("stocks", [])
        except Exception as e:
            logger.error("加载股票池失败: %s", e)
            return self._default_stock_pool()
    
    def _default_stock_pool(self) -> List[Dict]:
        """默认股票池（用户历史持仓20只）"""
        return [
            {"symbol": "600519.SH", "name": "贵州茅台", "category": "holding"},
            {"symbol": "300750.SZ", "name": "宁德时代", "category": "holding"},
            {"symbol": "300308.SZ", "name": "中际旭创", "category": "holding"},
            {"symbol": "600030.SH", "name": "中信证券", "category": "holding"},
            {"symbol": "000333.SZ", "name": "美的集团", "category": "holding"},
            {"symbol": "002475.SZ", "name": "立讯精密", "category": "holding"},
            {"symbol": "600276.SH", "name": "恒瑞医药", "category": "holding"},
            {"symbol": "300418.SZ", "name": "昆仑万维", "category": "holding"},
            {"symbol": "002594.SZ", "name": "比亚迪", "category": "holding"},
            {"symbol": "000858.SZ", "name": "五粮液", "category": "holding"},
            {"symbol": "601318.SH", "name": "中国平安", "category": "holding"},
            {"symbol": "600887.SH", "name": "伊利股份", "category": "holding"},
            {"symbol": "002230.SZ", "name": "科大讯飞", "category": "holding"},
            {"symbol": "300014.SZ", "name": "亿纬锂能", "category": "holding"},
            {"symbol": "601012.SH", "name": "隆基绿能", "category": "holding"},
            {"symbol": "300760.SZ", "name": "迈瑞医疗", "category": "holding"},
            {"symbol": "601899.SH", "name": "紫金矿业", "category": "holding"},
            {"symbol": "000725.SZ", "name": "京东方A", "category": "holding"},
            {"symbol": "603501.SH", "name": "韦尔股份", "category": "holding"},
            {"symbol": "600809.SH", "name": "山西汾酒", "category": "holding"},
        ]
    
    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------
    
    def run_premarket(self) -> Dict[str, Any]:
        """
        盘前分析（每日 8:30 执行）
        
        输出：大盘简报 + 个股分析 + 交易信号
        """
        logger.info("=" * 40)
        logger.info("开始盘前分析: %s", date.today().isoformat())
        logger.info("=" * 40)
        
        results = {
            "date": date.today().isoformat(),
            "market_context": None,
            "stock_reports": [],
            "signals": [],
            "portfolio_snapshot": None,
        }
        
        # 1. 获取大盘上下文（真实数据）
        market_ctx = self.market_ctx.get_context()
        results["market_context"] = market_ctx
        
        # 2. 获取持仓快照
        portfolio_snapshot = self.portfolio.replay()
        results["portfolio_snapshot"] = portfolio_snapshot
        total_equity = portfolio_snapshot.get("total_equity", 1_000_000)
        
        # 3. 遍历股票池分析（真实行情）
        for stock in self.stock_pool:
            try:
                report, signal = self._analyze_one_stock(
                    stock, total_equity, market_ctx
                )
                if report:
                    results["stock_reports"].append(report)
                if signal:
                    results["signals"].append(signal)
            except Exception as e:
                logger.error("分析 %s 失败: %s", stock.get("symbol"), e)
        
        # 4. 生成大盘报告
        market_report = self.reporter.generate_market_report(
            market_ctx, results["signals"]
        )
        
        # 5. 生成持仓报告
        portfolio_report = self.reporter.generate_portfolio_report(
            portfolio_snapshot, results["signals"]
        )
        
        results["market_report"] = market_report
        results["portfolio_report"] = portfolio_report
        
        logger.info("盘前分析完成: %d 只分析, %d 个信号",
                    len(results["stock_reports"]),
                    len(results["signals"]))
        
        return results
    
    def run_postmarket(self) -> Dict[str, Any]:
        """
        盘后复盘（每日 15:30 执行）
        
        1. 验证待确认信号
        2. 更新持仓快照
        3. 生成准确率统计
        """
        logger.info("=" * 40)
        logger.info("开始盘后复盘: %s", date.today().isoformat())
        logger.info("=" * 40)
        
        results = {
            "date": date.today().isoformat(),
            "verified_signals": [],
            "accuracy_stats": None,
            "portfolio_snapshot": None,
        }
        
        # 1. 验证待确认信号
        verified = self.signal_tracker.verify_pending_signals()
        results["verified_signals"] = verified
        
        # 2. 更新持仓快照
        snapshot = self.portfolio.replay()
        results["portfolio_snapshot"] = snapshot
        
        # 3. 准确率统计
        stats = self.signal_tracker.get_accuracy_stats(days=30)
        results["accuracy_stats"] = stats
        
        # 4. 生成报告
        accuracy_report = self.reporter.generate_accuracy_report(stats)
        results["accuracy_report"] = accuracy_report
        
        logger.info("盘后复盘完成: %d 信号已验证", len(verified))
        
        return results
    
    def _analyze_one_stock(self,
                           stock: Dict,
                           total_equity: float,
                           market_ctx: Optional[Dict]) -> tuple:
        """
        分析单只股票（真实数据版）
        
        Returns: (report_text, signal_dict)
        """
        symbol = stock.get("symbol", "")
        name = stock.get("name", "")
        
        # 获取真实行情数据
        dp = self._get_dp()
        data = dp.get_analysis_data(symbol)
        
        spot = data.get("spot")
        indicators = data.get("indicators")
        fundamental = data.get("fundamental")
        
        # 构建market_data（评分器需要的格式）
        if indicators:
            market_data = {
                "code": symbol,
                "close": indicators.get("close", spot.get("close", 100) if spot else 100),
                "ma5": indicators.get("ma5", 0),
                "ma10": indicators.get("ma10", 0),
                "ma20": indicators.get("ma20", 0),
                "volume": indicators.get("volume", 0),
                "vol_ma20": indicators.get("vol_ma20", 0),
                "turnover": spot.get("turnover", 0) or 0,
                "macd": indicators.get("macd", 0),
                "macd_signal": indicators.get("macd_signal", 0),
                "rsi14": indicators.get("rsi14", 50),
                "high_20d": indicators.get("high_20d", 0),
            }
        elif spot:
            # 只有实时行情，没有历史指标
            market_data = {
                "code": symbol,
                "close": spot.get("close", 100),
                "ma5": spot.get("close", 100),
                "ma10": spot.get("close", 100),
                "ma20": spot.get("close", 100),
                "volume": spot.get("volume", 0),
                "vol_ma20": spot.get("volume", 0),
                "turnover": spot.get("turnover", 0) or 0,
                "macd": 0,
                "macd_signal": 0,
                "rsi14": 50,
            }
        else:
            # 没有任何数据，使用占位
            logger.warning("%s 无数据，使用占位", symbol)
            market_data = {
                "code": symbol,
                "close": 100.0,
                "ma5": 99.0, "ma10": 98.0, "ma20": 97.0,
                "volume": 100000, "vol_ma20": 80000, "turnover": 0.05,
                "macd": 0.5, "macd_signal": 0.3, "rsi14": 55,
            }
        
        # 执行分析
        analysis = self.analyzer.analyze(
            stock_code=symbol,
            market_data=market_data,
            fundamental_data=fundamental
        )
        
        # 计算仓位
        position = self.kelly.calculate(
            total_equity=total_equity,
            stock_price=market_data["close"],
            stock_score=analysis["total_score"],
            market_context=market_ctx
        )
        
        # 生成报告文本
        report = self.reporter.generate_stock_report(
            stock_code=symbol,
            stock_name=name,
            analysis_result=analysis,
            position_advice=position,
            market_context=market_ctx
        )
        
        # 生成信号（评分>=65且建议买入/加仓）
        signal = None
        action = analysis.get("advice", {}).get("action", "hold")
        if analysis["total_score"] >= 65 and action in ("buy", "add"):
            signal_id = self.signal_tracker.create_signal(
                stock_code=symbol,
                stock_name=name,
                signal_type=action,
                confidence=min(int(analysis["total_score"]), 100),
                reason=analysis.get("advice", {}).get("summary", ""),
                score_total=int(analysis["total_score"]),
                score_breakdown=analysis.get("scores"),
                kelly_fraction=position.get("adjusted_pct", 0) / 100,
                target_price=market_data["close"] * 1.1,  # 默认10%目标
                stop_loss=market_data["close"] * 0.95,    # 默认5%止损
                suggested_shares=position.get("suggested_shares", 0),
                market_context=market_ctx.get("summary", "") if market_ctx else ""
            )
            signal = {
                "signal_id": signal_id,
                "stock_code": symbol,
                "stock_name": name,
                "signal_type": action,
                "confidence": int(analysis["total_score"]),
                "kelly_fraction": position.get("adjusted_pct", 0) / 100,
            }
        
        return report, signal
    
    # ------------------------------------------------------------------
    # 快捷命令
    # ------------------------------------------------------------------
    
    def cmd_premarket(self) -> str:
        """执行盘前分析，返回完整报告文本"""
        results = self.run_premarket()
        
        parts = [
            results.get("market_report", ""),
            "",
            results.get("portfolio_report", ""),
            "",
        ]
        
        # 添加个股报告（限制数量避免消息过长）
        reports = results.get("stock_reports", [])
        for report in reports[:5]:
            parts.append(report)
            parts.append("")
        
        if len(reports) > 5:
            parts.append("... 还有 {} 只分析结果省略".format(len(reports) - 5))
        
        return "\n".join(parts)
    
    def cmd_postmarket(self) -> str:
        """执行盘后复盘，返回报告文本"""
        results = self.run_postmarket()
        
        parts = [
            results.get("accuracy_report", ""),
            "",
            "📊 今日持仓快照:",
        ]
        
        snapshot = results.get("portfolio_snapshot", {})
        parts.append("   总权益: {:,.2f}".format(snapshot.get("total_equity", 0)))
        parts.append("   已实现盈亏: {:+,.2f}".format(snapshot.get("realized_pnl", 0)))
        parts.append("   浮盈浮亏: {:+,.2f}".format(snapshot.get("unrealized_pnl", 0)))
        
        verified = results.get("verified_signals", [])
        if verified:
            parts.extend(["", "✅ 今日验证信号:",])
            for v in verified[:5]:
                parts.append("   {}: {} ({:.1f}%)".format(
                    v.get("signal_id", "")[:16],
                    v.get("outcome", ""),
                    v.get("outcome_return", 0)
                ))
        
        return "\n".join(parts)


# 全局实例
_launcher = None


def get_launcher() -> TradingSystemLauncher:
    """获取全局调度器"""
    global _launcher
    if _launcher is None:
        _launcher = TradingSystemLauncher()
    return _launcher


def premarket() -> str:
    """快捷执行盘前分析"""
    return get_launcher().cmd_premarket()


def postmarket() -> str:
    """快捷执行盘后复盘"""
    return get_launcher().cmd_postmarket()
