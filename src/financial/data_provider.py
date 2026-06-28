# -*- coding: utf-8 -*-
"""
A股数据适配层（混合源）
核心能力：
1. 实时行情：新浪API（轻量，单只股票）
2. 历史K线：akshare（技术指标计算）
3. 基本面：akshare个股指标
4. 指数数据：新浪API

Python 3.6+ 兼容
"""

import logging
import json
import requests
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)


class DataProvider:
    """
    混合数据源提供者
    
    实时行情 -> 新浪API（秒级，轻量）
    历史K线 -> akshare（前复权）
    基本面 -> akshare
    """
    
    def __init__(self):
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://finance.sina.com.cn",
        })
        self._ak = None
        self._pd = None
    
    def _init_akshare(self):
        """延迟初始化akshare"""
        if self._ak is None:
            import akshare as ak
            self._ak = ak
        if self._pd is None:
            import pandas as pd
            self._pd = pd
    
    # ------------------------------------------------------------------
    # 新浪API实时行情（轻量）
    # ------------------------------------------------------------------
    
    def get_stock_spot_sina(self, symbol: str) -> Optional[Dict[str, Any]]:
        """
        新浪API获取个股实时行情
        
        URL格式: https://hq.sinajs.cn/list=sh600519
        返回格式: var hq_str_sh600519="贵州茅台,1745.000,...";
        """
        try:
            code = symbol.split(".")[0] if "." in symbol else symbol
            
            # 判断市场前缀
            if symbol.endswith(".SH") or (len(code) == 6 and code.startswith(("6", "5", "9"))):
                prefix = "sh"
            else:
                prefix = "sz"
            
            url = "https://hq.sinajs.cn/list={}{}".format(prefix, code)
            resp = self._session.get(url, timeout=10)
            resp.encoding = "gb2312"
            
            text = resp.text
            if not text or "hq_str_" not in text:
                return None
            
            # 解析数据
            # var hq_str_sh600519="贵州茅台,1745.000,1750.000,1748.000,...";
            data_part = text.split('"')[1]
            fields = data_part.split(",")
            
            if len(fields) < 33:
                logger.warning("新浪数据字段不足: %s", symbol)
                return None
            
            # 字段映射（新浪格式）
            # 0=名称, 1=今日开盘价, 2=昨日收盘价, 3=当前价, 4=最高价, 5=最低价
            # 8=成交量(股), 9=成交额(元)
            # 其余字段略
            
            name = fields[0]
            open_price = self._to_float(fields[1])
            pre_close = self._to_float(fields[2])
            close = self._to_float(fields[3])
            high = self._to_float(fields[4])
            low = self._to_float(fields[5])
            volume = self._to_float(fields[8])  # 股
            amount = self._to_float(fields[9])  # 元
            
            change_pct = ((close - pre_close) / pre_close * 100) if pre_close > 0 else 0
            
            return {
                "symbol": symbol,
                "code": code,
                "name": name,
                "close": close,
                "open": open_price,
                "high": high,
                "low": low,
                "pre_close": pre_close,
                "change_pct": round(change_pct, 2),
                "volume": volume,
                "amount": amount,
                "turnover": None,  # 新浪不直接提供
            }
        except Exception as e:
            logger.error("新浪API获取 %s 失败: %s", symbol, e)
            return None
    
    def get_index_spot_sina(self, index_code: str = "000001") -> Optional[Dict[str, Any]]:
        """新浪API获取指数实时行情"""
        try:
            prefix = "sh" if index_code.startswith(("0", "6")) else "sz"
            url = "https://hq.sinajs.cn/list=s_{}{}".format(prefix, index_code)
            resp = self._session.get(url, timeout=10)
            resp.encoding = "gb2312"
            
            text = resp.text
            if not text or "hq_str_s_" not in text:
                return None
            
            data_part = text.split('"')[1]
            fields = data_part.split(",")
            
            if len(fields) < 5:
                return None
            
            # 0=名称, 1=当前点数, 2=涨跌额, 3=涨跌幅, 4=成交量, 5=成交额
            return {
                "code": index_code,
                "name": fields[0],
                "close": self._to_float(fields[1]),
                "change": self._to_float(fields[2]),
                "change_pct": self._to_float(fields[3]),
            }
        except Exception as e:
            logger.error("新浪API获取指数 %s 失败: %s", index_code, e)
            return None
    
    # ------------------------------------------------------------------
    # akshare历史K线（技术指标）
    # ------------------------------------------------------------------
    
    def get_stock_hist(self, symbol: str, days: int = 60) -> Optional[List[Dict]]:
        """获取历史K线数据"""
        try:
            self._init_akshare()
            code = symbol.split(".")[0] if "." in symbol else symbol
            
            df = self._ak.stock_zh_a_hist(
                symbol=code,
                period="daily",
                start_date=(date.today() - timedelta(days=days + 10)).strftime("%Y%m%d"),
                end_date=date.today().strftime("%Y%m%d"),
                adjust="qfq"
            )
            
            if df is None or df.empty:
                return None
            
            records = []
            for _, row in df.iterrows():
                records.append({
                    "date": str(row.get("日期", "")),
                    "open": self._to_float(row.get("开盘", 0)),
                    "close": self._to_float(row.get("收盘", 0)),
                    "high": self._to_float(row.get("最高", 0)),
                    "low": self._to_float(row.get("最低", 0)),
                    "volume": self._to_float(row.get("成交量", 0)),
                    "amount": self._to_float(row.get("成交额", 0)),
                })
            
            return records
        except Exception as e:
            logger.error("获取 %s 历史数据失败: %s", symbol, e)
            return None
    
    def calc_technical_indicators(self, hist_data: List[Dict]) -> Dict[str, Any]:
        """计算技术指标"""
        if not hist_data or len(hist_data) < 20:
            return {}
        
        try:
            self._init_akshare()
            import pandas as pd
            import numpy as np
            
            df = pd.DataFrame(hist_data)
            for col in ["close", "high", "low", "volume"]:
                df[col] = pd.to_numeric(df[col], errors="coerce")
            
            closes = df["close"].values
            volumes = df["volume"].values
            
            # MA
            ma5 = closes[-5:].mean() if len(closes) >= 5 else closes[-1]
            ma10 = closes[-10:].mean() if len(closes) >= 10 else closes[-1]
            ma20 = closes[-20:].mean() if len(closes) >= 20 else closes[-1]
            
            # 成交量MA
            vol_ma20 = volumes[-20:].mean() if len(volumes) >= 20 else volumes[-1]
            
            # MACD (简化版)
            ema12 = pd.Series(closes).ewm(span=12, adjust=False).mean().values
            ema26 = pd.Series(closes).ewm(span=26, adjust=False).mean().values
            macd_line = ema12 - ema26
            macd_signal_line = pd.Series(macd_line).ewm(span=9, adjust=False).mean().values
            
            # RSI
            rsi = self._calc_rsi(closes, 14)
            
            # 20日高低点（用于背离检测）
            high_20d = df["high"].tail(20).max()
            
            latest = hist_data[-1]
            
            return {
                "close": latest["close"],
                "ma5": round(float(ma5), 3),
                "ma10": round(float(ma10), 3),
                "ma20": round(float(ma20), 3),
                "volume": latest.get("volume", 0),
                "vol_ma20": round(float(vol_ma20), 0),
                "macd": round(float(macd_line[-1]), 4) if len(macd_line) > 0 else 0,
                "macd_signal": round(float(macd_signal_line[-1]), 4) if len(macd_signal_line) > 0 else 0,
                "rsi14": round(float(rsi), 2) if rsi else 50,
                "high_20d": round(float(high_20d), 2) if high_20d else None,
            }
        except Exception as e:
            logger.error("计算技术指标失败: %s", e)
            return {}
    
    def _calc_rsi(self, closes, period: int = 14):
        """计算RSI"""
        if len(closes) < period + 1:
            return None
        import pandas as pd
        deltas = pd.Series(closes).diff().dropna()
        gain = deltas.where(deltas > 0, 0).rolling(window=period).mean()
        loss = (-deltas.where(deltas < 0, 0)).rolling(window=period).mean()
        if len(loss) == 0 or loss.iloc[-1] == 0:
            return 100
        rs = gain.iloc[-1] / loss.iloc[-1]
        return 100 - (100 / (1 + rs))
    
    # ------------------------------------------------------------------
    # akshare基本面（轻量：只取单只股票相关）
    # ------------------------------------------------------------------
    
    def get_fundamental(self, symbol: str,
                          current_price: Optional[float] = None) -> Optional[Dict[str, Any]]:
        """获取基本面数据（接入fundamental_data模块）"""
        try:
            from .fundamental_data import get_fundamental_data
            return get_fundamental_data(symbol, current_price)
        except Exception as e:
            logger.error("获取 %s 基本面失败: %s", symbol, e)
            return None
    
    # ------------------------------------------------------------------
    # 聚合接口：获取分析所需全部数据
    # ------------------------------------------------------------------
    
    def get_analysis_data(self, symbol: str) -> Dict[str, Any]:
        """
        获取个股分析所需的全部数据
        
        Returns:
            {
                "spot": 实时行情,
                "indicators": 技术指标,
                "fundamental": 基本面,
            }
        """
        result = {
            "spot": None,
            "indicators": None,
            "fundamental": None,
        }
        
        # 1. 实时行情
        spot = self.get_stock_spot_sina(symbol)
        if spot:
            result["spot"] = spot
        
        # 2. 历史数据+技术指标
        hist = self.get_stock_hist(symbol, days=40)
        if hist:
            indicators = self.calc_technical_indicators(hist)
            if indicators:
                # 合并实时行情（更准的收盘价）
                if spot:
                    indicators["close"] = spot["close"]
                result["indicators"] = indicators
        
        # 3. 基本面（传入当前价格用于计算PE/PB）
        current_price = spot.get("close") if spot else None
        fundamental = self.get_fundamental(symbol, current_price)
        if fundamental:
            result["fundamental"] = fundamental
        
        return result
    
    # ------------------------------------------------------------------
    # 工具
    # ------------------------------------------------------------------
    
    def _to_float(self, val, default: float = 0.0) -> float:
        if val is None:
            return default
        try:
            return float(val)
        except (ValueError, TypeError):
            return default


# 全局实例
_provider = None


def get_data_provider() -> DataProvider:
    global _provider
    if _provider is None:
        _provider = DataProvider()
    return _provider
