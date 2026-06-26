#!/usr/bin/env python3
"""
数据集清洗流水线演示

业务场景：爬虫采集的原始数据通常包含缺失值、重复项、格式不一致等问题，
清洗后的数据质量直接影响下游AI模型的效果。

技术要点：
1. 数据质量评估：清洗前/后对比（量化）
2. 多阶段清洗：去重→补全→标准化→校验
3. 异常检测：统计方法+规则引擎
"""
import pandas as pd
import numpy as np
import json
import re
from datetime import datetime
from typing import Dict, List, Tuple


class DataCleaner:
    """数据集清洗流水线"""
    
    def __init__(self):
        self.stats = {}
    
    def assess_quality(self, df: pd.DataFrame) -> Dict:
        """评估数据质量（清洗前）"""
        total = len(df)
        
        # 各字段缺失率
        missing_rates = (df.isnull().sum() / total * 100).to_dict()
        
        # 重复率
        duplicate_rate = df.duplicated().sum() / total * 100
        
        # 数值字段异常值（IQR方法）
        outliers = {}
        for col in df.select_dtypes(include=[np.number]).columns:
            Q1 = df[col].quantile(0.25)
            Q3 = df[col].quantile(0.75)
            IQR = Q3 - Q1
            outlier_count = ((df[col] < (Q1 - 1.5 * IQR)) | (df[col] > (Q3 + 1.5 * IQR))).sum()
            outliers[col] = outlier_count / total * 100
        
        return {
            "total_rows": total,
            "missing_rates": missing_rates,
            "duplicate_rate": duplicate_rate,
            "outlier_rates": outliers,
            "quality_score": self._quality_score(missing_rates, duplicate_rate, outliers)
        }
    
    def _quality_score(self, missing: Dict, duplicate: float, outliers: Dict) -> float:
        """综合质量评分（0-100）"""
        # 缺失率惩罚
        missing_penalty = sum(missing.values()) / len(missing) if missing else 0
        # 重复率惩罚
        dup_penalty = duplicate
        # 异常率惩罚
        outlier_penalty = sum(outliers.values()) / len(outliers) if outliers else 0
        
        score = 100 - (missing_penalty * 0.4 + dup_penalty * 0.3 + outlier_penalty * 0.3)
        return max(0, round(score, 2))
    
    def remove_duplicates(self, df: pd.DataFrame, subset: List[str] = None) -> Tuple[pd.DataFrame, int]:
        """去重：基于关键字段"""
        before = len(df)
        df_clean = df.drop_duplicates(subset=subset, keep="first")
        removed = before - len(df_clean)
        return df_clean, removed
    
    def fill_missing(self, df: pd.DataFrame, rules: Dict) -> pd.DataFrame:
        """缺失值填充策略
        
        rules: {"字段名": "策略"}
        策略：mean(均值)/median(中位数)/mode(众数)/constant(固定值)/ffill(前向填充)
        """
        df = df.copy()
        for col, strategy in rules.items():
            if col not in df.columns:
                continue
            if strategy == "mean":
                df[col].fillna(df[col].mean(), inplace=True)
            elif strategy == "median":
                df[col].fillna(df[col].median(), inplace=True)
            elif strategy == "mode":
                df[col].fillna(df[col].mode()[0] if not df[col].mode().empty else "", inplace=True)
            elif strategy == "ffill":
                df[col].fillna(method="ffill", inplace=True)
            elif strategy.startswith("constant:"):
                val = strategy.split(":", 1)[1]
                df[col].fillna(val, inplace=True)
        return df
    
    def standardize(self, df: pd.DataFrame, rules: Dict) -> pd.DataFrame:
        """标准化：格式统一、单位转换、类型转换"""
        df = df.copy()
        
        for col, rule in rules.items():
            if col not in df.columns:
                continue
            
            if rule == "price":
                # 价格标准化：统一为数字，去掉单位
                df[col] = df[col].astype(str).apply(self._parse_price)
            elif rule == "date":
                # 日期标准化：统一为YYYY-MM-DD
                df[col] = pd.to_datetime(df[col], errors="coerce")
            elif rule == "category":
                # 类别标准化：统一大小写、去空格
                df[col] = df[col].astype(str).str.strip().str.upper()
            elif rule == "url":
                # URL标准化：补全协议头
                df[col] = df[col].astype(str).apply(
                    lambda x: x if x.startswith("http") else f"https://{x}" if x else ""
                )
        
        return df
    
    def _parse_price(self, val) -> float:
        """解析价格：提取数字，处理各种格式"""
        if pd.isna(val):
            return np.nan
        text = str(val)
        # 提取数字（支持万、亿、千等单位）
        match = re.search(r'(\d+(?:\.\d+)?)\s*(万|亿|千)?', text)
        if not match:
            return np.nan
        
        num = float(match.group(1))
        unit = match.group(2)
        
        if unit == "万":
            num *= 10000
        elif unit == "亿":
            num *= 100000000
        elif unit == "千":
            num *= 1000
        
        return num
    
    def detect_anomalies(self, df: pd.DataFrame, rules: Dict) -> pd.DataFrame:
        """异常检测：标记问题数据"""
        df = df.copy()
        df["is_anomaly"] = False
        df["anomaly_reason"] = ""
        
        for col, rule in rules.items():
            if col not in df.columns:
                continue
            
            if rule["type"] == "range":
                # 范围检查：超出min/max标记
                min_val, max_val = rule["min"], rule["max"]
                mask = (df[col] < min_val) | (df[col] > max_val)
                df.loc[mask, "is_anomaly"] = True
                df.loc[mask, "anomaly_reason"] += f"{col}超出范围;"
            
            elif rule["type"] == "regex":
                # 正则检查：格式不匹配标记
                pattern = rule["pattern"]
                mask = ~df[col].astype(str).str.match(pattern, na=False)
                df.loc[mask, "is_anomaly"] = True
                df.loc[mask, "anomaly_reason"] += f"{col}格式错误;"
        
        return df
    
    def process_pipeline(self, df: pd.DataFrame, config: Dict) -> Tuple[pd.DataFrame, Dict]:
        """完整清洗流水线"""
        # 1. 评估原始质量
        before_quality = self.assess_quality(df)
        
        # 2. 去重
        if config.get("dedup"):
            df, dup_removed = self.remove_duplicates(df, subset=config["dedup"].get("subset"))
            print(f"[去重] 移除 {dup_removed} 条重复数据")
        
        # 3. 填充缺失值
        if config.get("fill"):
            df = self.fill_missing(df, config["fill"])
            print(f"[填充] 缺失值填充完成")
        
        # 4. 标准化
        if config.get("standardize"):
            df = self.standardize(df, config["standardize"])
            print(f"[标准化] 格式统一完成")
        
        # 5. 异常检测
        if config.get("anomaly"):
            df = self.detect_anomalies(df, config["anomaly"])
            anomaly_count = df["is_anomaly"].sum()
            print(f"[异常检测] 标记 {anomaly_count} 条异常数据")
        
        # 6. 评估清洗后质量
        after_quality = self.assess_quality(df[~df.get("is_anomaly", False)])
        
        report = {
            "before": before_quality,
            "after": after_quality,
            "improvement": round(after_quality["quality_score"] - before_quality["quality_score"], 2),
            "rows_before": before_quality["total_rows"],
            "rows_after": len(df[~df.get("is_anomaly", False)])
        }
        
        return df, report


def generate_mock_data(n: int = 1000) -> pd.DataFrame:
    """生成模拟的交易所产品数据（含脏数据）"""
    np.random.seed(42)
    
    # 基础数据
    sources = ["cantonde", "bjidex", "zjdex", "shexchange", "hundata"]
    categories = ["数据服务", "API接口", "数据报告", "", "数据服务", "未知"]
    
    data = {
        "source": np.random.choice(sources, n),
        "name": [f"产品_{i}" for i in range(n)],
        "category": np.random.choice(categories, n),
        "price": np.random.choice([
            "5000元", "1.2万", "8000", None, "面议", "3000", "invalid", "2.5万", "", "10000"
        ], n),
        "provider": np.random.choice(["阿里", "腾讯", "华为", None, "", "百度"], n),
        "url": np.random.choice([
            "https://example.com/1", "example.com/2", "http://test.com/3", None, "", "https://site.com/4"
        ], n),
        "created_at": np.random.choice([
            "2024-01-15", "2024/02/20", None, "invalid_date", "2024-03-10", "2024-04-01"
        ], n)
    }
    
    # 添加重复数据（10%）
    df = pd.DataFrame(data)
    duplicates = df.sample(int(n * 0.1), random_state=42)
    df = pd.concat([df, duplicates], ignore_index=True)
    
    return df


if __name__ == "__main__":
    # 1. 生成模拟数据
    print("=" * 50)
    print("数据集清洗流水线演示")
    print("=" * 50)
    
    df_raw = generate_mock_data(1000)
    print(f"[原始数据] {len(df_raw)} 条")
    print(df_raw.head(3))
    
    # 2. 配置清洗规则
    config = {
        "dedup": {"subset": ["source", "name", "price"]},
        "fill": {
            "category": "constant:未分类",
            "provider": "constant:未知",
            "url": "constant:"
        },
        "standardize": {
            "price": "price",
            "created_at": "date",
            "category": "category",
            "url": "url"
        },
        "anomaly": {
            "price": {"type": "range", "min": 1000, "max": 1000000},
            "url": {"type": "regex", "pattern": r"^https?://.+"}
        }
    }
    
    # 3. 执行清洗
    cleaner = DataCleaner()
    df_clean, report = cleaner.process_pipeline(df_raw, config)
    
    # 4. 输出报告
    print("\n" + "=" * 50)
    print("清洗报告")
    print("=" * 50)
    print(f"原始数据量: {report['rows_before']}")
    print(f"清洗后数据量: {report['rows_after']}")
    print(f"质量评分提升: {report['before']['quality_score']} → {report['after']['quality_score']} (+{report['improvement']})")
    print(f"\nTop-3 缺失字段（清洗前）:")
    missing_before = sorted(report['before']['missing_rates'].items(), key=lambda x: x[1], reverse=True)[:3]
    for field, rate in missing_before:
        print(f"  {field}: {rate:.1f}%")
