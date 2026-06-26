#!/usr/bin/env python3
"""
消费品AI应用场景设计

业务场景：植护（纸品电商）的AI落地场景
- 纸品库存预测：基于历史销售+季节性预测补货
- 用户评论情感分析：洞察产品问题
- 供应链优化：前置仓库存分配

设计思路：
1. 从真实业务痛点出发（不是"为了用AI而用AI"）
2. 可量化目标（节省2小时/提升5%CTR）
3. 快速验证MVP（1周内出原型）
"""
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import List, Dict, Tuple
import re
from collections import Counter


class InventoryPredictor:
    """纸品库存预测器
    
    业务痛点：
    - 纸品是快消品，销量波动大（双11、春节、疫情等）
    - 库存过多→仓储成本增加；库存不足→断货损失
    - 传统人工预测依赖经验，准确率约60%
    
    AI方案：时间序列+季节因子+促销活动，预测未来7-30天销量
    """
    
    def __init__(self):
        self.season_factors = {
            1: 1.2,   # 春节（1月）
            2: 0.9,   # 节后回落
            6: 1.1,   # 618大促
            11: 1.5,  # 双11
            12: 1.3,  # 双12+年货节
        }
    
    def generate_mock_sales(self, days: int = 365) -> pd.DataFrame:
        """生成模拟销售数据（含季节性+趋势+噪声）"""
        np.random.seed(42)
        
        dates = [datetime(2024, 1, 1) + timedelta(days=i) for i in range(days)]
        
        # 基础销量（趋势增长）
        base = 1000 + np.arange(days) * 0.5
        
        # 季节性
        seasonal = []
        for d in dates:
            factor = self.season_factors.get(d.month, 1.0)
            # 周效应：周末销量高
            if d.weekday() >= 5:
                factor *= 1.15
            seasonal.append(factor)
        
        # 随机噪声
        noise = np.random.normal(0, 50, days)
        
        # 促销事件（随机）
        promo = np.zeros(days)
        promo_days = np.random.choice(days, 20, replace=False)
        for p in promo_days:
            promo[p:p+3] = np.random.uniform(200, 500)
        
        sales = base * np.array(seasonal) + noise + promo
        sales = np.maximum(sales, 0)  # 销量不能为负
        
        return pd.DataFrame({
            "date": dates,
            "sales": sales.astype(int),
            "is_promo": promo > 0,
            "month": [d.month for d in dates],
            "weekday": [d.weekday() for d in dates]
        })
    
    def predict(self, history: pd.DataFrame, forecast_days: int = 7) -> pd.DataFrame:
        """预测未来销量
        
        算法：移动平均 + 季节因子 + 趋势外推
        （实际业务中可替换为Prophet/LSTM/ARIMA）
        """
        # 计算最近30天移动平均
        recent_avg = history["sales"].tail(30).mean()
        
        # 趋势（最近7天 vs 前7天）
        trend = history["sales"].tail(7).mean() / history["sales"].iloc[-14:-7].mean()
        trend = max(0.8, min(1.2, trend))  # 限制趋势幅度
        
        # 预测
        last_date = history["date"].iloc[-1]
        predictions = []
        
        for i in range(1, forecast_days + 1):
            future_date = last_date + timedelta(days=i)
            
            # 基础预测
            pred = recent_avg * trend
            
            # 季节因子
            month_factor = self.season_factors.get(future_date.month, 1.0)
            pred *= month_factor
            
            # 周末因子
            if future_date.weekday() >= 5:
                pred *= 1.15
            
            predictions.append({
                "date": future_date,
                "predicted_sales": int(pred),
                "confidence": "high" if 0.9 <= trend <= 1.1 else "medium"
            })
        
        return pd.DataFrame(predictions)
    
    def evaluate(self, actual: pd.DataFrame, predicted: pd.DataFrame) -> Dict:
        """评估预测准确率"""
        merged = pd.merge(actual, predicted, on="date")
        if len(merged) == 0:
            return {"mape": None, "note": "无重叠数据"}
        
        mape = (abs(merged["sales"] - merged["predicted_sales"]) / merged["sales"]).mean() * 100
        
        return {
            "mape": round(mape, 2),
            "avg_actual": int(merged["sales"].mean()),
            "avg_predicted": int(merged["predicted_sales"].mean()),
            "accuracy": round(100 - mape, 2)
        }


class ReviewAnalyzer:
    """用户评论情感分析
    
    业务痛点：
    - 纸品评论量大（年用户1亿+），人工看不过来
    - 差评分散在不同平台（天猫/拼多多/抖音）
    - 需要快速定位产品问题（质量/包装/物流）
    
    AI方案：批量情感分类 + 问题聚类 + 关键词提取
    """
    
    def __init__(self):
        # 问题关键词库（可扩展）
        self.issue_keywords = {
            "质量": ["薄", "容易破", "掉屑", "粗糙", "质量差"],
            "包装": ["包装破损", "漏发", "包装简陋", "压扁"],
            "物流": ["送货慢", "快递差", "压坏", "物流慢"],
            "尺寸": ["太小", "尺寸不符", "比图片小", "不够大"],
            "价格": ["涨价", "贵", "不值", "不划算"]
        }
    
    def analyze_batch(self, reviews: List[str]) -> Dict:
        """批量分析评论"""
        results = []
        
        for review in reviews:
            # 情感判断（简单规则版，实际可用BERT/DeepSeek）
            positive = ["好", "不错", "满意", "推荐", "质量好", "柔软"]
            negative = ["差", "不好", "失望", "退货", "烂", "薄", "破"]
            
            pos_count = sum(1 for p in positive if p in review)
            neg_count = sum(1 for n in negative if n in review)
            
            if neg_count > pos_count:
                sentiment = "negative"
            elif pos_count > neg_count:
                sentiment = "positive"
            else:
                sentiment = "neutral"
            
            # 问题分类
            issues = []
            for category, keywords in self.issue_keywords.items():
                if any(kw in review for kw in keywords):
                    issues.append(category)
            
            results.append({
                "review": review[:50],
                "sentiment": sentiment,
                "issues": issues
            })
        
        # 统计
        sentiment_dist = Counter(r["sentiment"] for r in results)
        issue_dist = Counter()
        for r in results:
            for i in r["issues"]:
                issue_dist[i] += 1
        
        return {
            "total": len(reviews),
            "sentiment_distribution": dict(sentiment_dist),
            "issue_distribution": dict(issue_dist),
            "negative_rate": round(sentiment_dist.get("negative", 0) / len(reviews) * 100, 2),
            "top_issue": issue_dist.most_common(1)[0] if issue_dist else None
        }


class SupplyChainOptimizer:
    """供应链优化：前置仓库存分配
    
    业务痛点：
    - 100+前置仓，各区域需求差异大
    - 调货成本高，库存积压和缺货并存
    
    AI方案：基于需求预测+调货成本，最优分配
    """
    
    def optimize_allocation(self, 
                           warehouse_demands: Dict[str, int],
                           warehouse_inventory: Dict[str, int],
                           transfer_cost: float = 10.0) -> Dict:
        """优化库存分配
        
        目标：最小化总成本 = 缺货损失 + 调货成本 + 库存持有成本
        """
        total_demand = sum(warehouse_demands.values())
        total_inventory = sum(warehouse_inventory.values())
        
        if total_inventory < total_demand:
            # 总库存不足：按优先级分配
            print("[警告] 总库存不足，需要紧急补货")
        
        # 简单策略：按需分配，余量集中到需求波动大的仓
        allocation = {}
        for wh, demand in warehouse_demands.items():
            available = warehouse_inventory.get(wh, 0)
            
            if available >= demand * 1.2:  # 20%安全库存
                allocation[wh] = {
                    "allocated": demand,
                    "safety_stock": available - demand,
                    "status": "充足"
                }
            elif available >= demand:
                allocation[wh] = {
                    "allocated": demand,
                    "safety_stock": 0,
                    "status": "紧平衡"
                }
            else:
                shortage = demand - available
                allocation[wh] = {
                    "allocated": available,
                    "shortage": shortage,
                    "status": "缺货"
                }
        
        return {
            "allocation": allocation,
            "total_cost": self._calculate_cost(allocation, transfer_cost)
        }
    
    def _calculate_cost(self, allocation: Dict, transfer_cost: float) -> Dict:
        """计算总成本"""
        shortage_cost = sum(
            a.get("shortage", 0) * 50  # 缺货损失50元/单位
            for a in allocation.values()
        )
        
        holding_cost = sum(
            a.get("safety_stock", 0) * 2  # 库存持有2元/单位
            for a in allocation.values()
        )
        
        return {
            "shortage_cost": shortage_cost,
            "holding_cost": holding_cost,
            "total": shortage_cost + holding_cost
        }


if __name__ == "__main__":
    print("=" * 60)
    print("消费品AI应用场景演示")
    print("=" * 60)
    
    # 1. 库存预测
    print("\n[场景1] 纸品库存预测")
    predictor = InventoryPredictor()
    
    # 生成历史数据
    history = predictor.generate_mock_sales(365)
    print(f"历史数据: {len(history)} 天")
    print(f"平均日销量: {history['sales'].mean():.0f}")
    print(f"销量峰值: {history['sales'].max()} (双11/春节)")
    
    # 预测未来7天
    forecast = predictor.predict(history, 7)
    print("\n未来7天预测:")
    print(forecast.to_string(index=False))
    
    # 2. 评论分析
    print("\n\n[场景2] 用户评论情感分析")
    analyzer = ReviewAnalyzer()
    
    mock_reviews = [
        "纸质很好，柔软不掉屑，回购很多次了",
        "包装太简陋，收到货箱子都破了",
        "比以前薄了，质量下降，考虑换品牌",
        "物流很快，第二天就到了，纸也不错",
        "价格涨太快，不划算了，下次等活动",
        "尺寸比图片小，感觉不够大，失望"
    ]
    
    result = analyzer.analyze_batch(mock_reviews)
    print(f"总评论: {result['total']}")
    print(f"负面率: {result['negative_rate']}%")
    print(f"情感分布: {result['sentiment_distribution']}")
    print(f"问题分布: {result['issue_distribution']}")
    print(f"首要问题: {result['top_issue']}")
    
    # 3. 供应链优化
    print("\n\n[场景3] 前置仓库存分配优化")
    optimizer = SupplyChainOptimizer()
    
    demands = {
        "华东仓": 5000,
        "华南仓": 3000,
        "华北仓": 4000,
        "西南仓": 2000
    }
    
    inventory = {
        "华东仓": 6000,
        "华南仓": 2500,
        "华北仓": 5000,
        "西南仓": 1800
    }
    
    result = optimizer.optimize_allocation(demands, inventory)
    print("分配方案:")
    for wh, alloc in result["allocation"].items():
        print(f"  {wh}: {alloc}")
    print(f"总成本: {result['total_cost']}")
