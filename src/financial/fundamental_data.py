# -*- coding: utf-8 -*-
"""
基本面数据获取模块（akshare版）
提供：PE/PB/ROE/营收增速/净利润增速/换手率/总市值 等

策略：
1. 财务数据（ROE/增速/毛利率）→ akshare.stock_yjbb_em（业绩报表）
2. PE/PB → 用 股价 / yjbb的每股收益/每股净资产 计算
3. 换手率/市值 → 暂用占位，待接入实时接口

Python 3.6+ 兼容
"""

import logging
from typing import Dict, Optional, Any

logger = logging.getLogger(__name__)


class FundamentalDataProvider:
    """基本面数据提供者"""
    
    def __init__(self):
        self._ak = None
    
    def _get_ak(self):
        if self._ak is None:
            import akshare as ak
            self._ak = ak
        return self._ak
    
    def get_fundamental(self, symbol: str,
                        current_price: Optional[float] = None) -> Optional[Dict[str, Any]]:
        """
        获取单只股票基本面数据
        
        Args:
            symbol: 股票代码（如 600519.SH）
            current_price: 当前股价（用于计算PE/PB，不传入则无法计算PE/PB）
        
        Returns:
            {
                "pe_ttm": float,      # 滚动市盈率（计算值）
                "pb": float,          # 市净率（计算值）
                "roe": float,         # 净资产收益率(%)
                "revenue_growth": float,  # 营收增速(%)
                "profit_growth": float,   # 净利润增速(%)
                "gross_margin": float,    # 毛利率(%)
                "eps": float,             # 每股收益
                "bps": float,             # 每股净资产
                "source": str,
            }
        """
        code = symbol.split(".")[0] if "." in symbol else symbol
        
        # 从业绩报表获取财务数据
        fin = self._get_financial_from_yjbb(code)
        if not fin:
            return None
        
        result = {
            "symbol": symbol,
            "code": code,
            "source": "akshare_yjbb",
        }
        result.update(fin)
        
        # 用股价计算PE/PB
        if current_price and current_price > 0:
            eps = fin.get("eps")
            bps = fin.get("bps")
            
            if eps and eps > 0:
                result["pe_ttm"] = round(current_price / eps, 2)
            if bps and bps > 0:
                result["pb"] = round(current_price / bps, 2)
        
        return result
    
    def _get_financial_from_yjbb(self, code: str) -> Optional[Dict[str, Any]]:
        """从业绩报表获取财务数据"""
        ak = self._get_ak()
        
        # 按优先级尝试报告期：年报 > 三季报 > 半年报 > 一季报
        for report_date in ["20241231", "20240930", "20240630", "20240331"]:
            try:
                df = ak.stock_yjbb_em(date=report_date)
                row = df[df["股票代码"] == code]
                if row.empty:
                    continue
                
                r = row.iloc[0]
                result = {}
                
                # 净资产收益率(ROE) — 转成小数
                roe = r.get("净资产收益率")
                if roe and self._is_valid_number(roe):
                    result["roe"] = float(roe) / 100
                
                # 营收同比增长
                rev = r.get("营业总收入-同比增长")
                if rev and self._is_valid_number(rev):
                    result["revenue_growth_yoy"] = float(rev) / 100  # 转成小数
                
                # 净利润同比增长
                profit = r.get("净利润-同比增长")
                if profit and self._is_valid_number(profit):
                    result["profit_growth_yoy"] = float(profit) / 100  # 转成小数
                
                # 销售毛利率
                gross = r.get("销售毛利率")
                if gross and self._is_valid_number(gross):
                    result["gross_margin"] = float(gross)
                
                # 每股收益
                eps = r.get("每股收益")
                if eps and self._is_valid_number(eps):
                    result["eps"] = float(eps)
                
                # 每股净资产
                bps = r.get("每股净资产")
                if bps and self._is_valid_number(bps):
                    result["bps"] = float(bps)
                
                if result:
                    return result
                    
            except Exception as e:
                logger.debug("yjbb %s 失败: %s", report_date, e)
                continue
        
        return None
    
    @staticmethod
    def _is_valid_number(val) -> bool:
        """检查是否为有效数字"""
        if val is None:
            return False
        s = str(val).strip()
        if s in ("-", "None", "nan", "", "--"):
            return False
        try:
            float(s)
            return True
        except:
            return False


# 全局实例
_fundamental_provider = None


def get_fundamental_provider() -> FundamentalDataProvider:
    global _fundamental_provider
    if _fundamental_provider is None:
        _fundamental_provider = FundamentalDataProvider()
    return _fundamental_provider


def get_fundamental_data(symbol: str,
                         current_price: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """便捷函数：获取单只股票基本面数据"""
    return get_fundamental_provider().get_fundamental(symbol, current_price)
