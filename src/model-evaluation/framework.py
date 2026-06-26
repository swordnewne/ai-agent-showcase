#!/usr/bin/env python3
"""
模型评测框架演示

业务场景：在金融新闻分析场景下，对比不同模型/配置的效果，
用数据驱动模型选型（而非"听说这个模型好"）。

评测维度：
1. 准确性：情感分类与人工标注的一致性
2. 鲁棒性：对输入扰动（错别字、省略）的稳定性
3. 效率：响应时间、Token消耗
4. 幻觉率：生成内容中事实错误的占比

技术要点：
- 标准化评测集：固定输入+预期输出
- 自动打分：规则引擎+LLM辅助评判
- 可视化报告：对比雷达图
"""
import json
import time
from typing import List, Dict, Tuple
from dataclasses import dataclass
import re


@dataclass
class TestCase:
    """评测用例"""
    id: str
    input_news: str          # 输入新闻标题
    expected_sentiment: float  # 期望情感分数(-1~1)
    expected_sectors: List[str]  # 期望影响板块
    difficulty: str            # 难度：easy/medium/hard


@dataclass
class ModelResult:
    """模型输出"""
    sentiment_score: float
    affected_sectors: List[str]
    raw_output: str
    latency_ms: float
    token_usage: int


class ModelEvaluator:
    """模型评测器"""
    
    def __init__(self):
        self.test_cases = self._load_test_cases()
    
    def _load_test_cases(self) -> List[TestCase]:
        """加载评测集（模拟真实业务场景）"""
        return [
            TestCase(
                id="T001",
                input_news="央行宣布降息25bp，市场流动性充裕",
                expected_sentiment=0.5,
                expected_sectors=["金融", "房地产"],
                difficulty="easy"
            ),
            TestCase(
                id="T002",
                input_news="新能源板块政策利好，宁德时代订单增长",
                expected_sentiment=0.8,
                expected_sectors=["新能源", "电池"],
                difficulty="easy"
            ),
            TestCase(
                id="T003",
                input_news="某公司财务造假被监管调查，股价暴跌",
                expected_sentiment=-0.8,
                expected_sectors=["监管", "个股风险"],
                difficulty="medium"
            ),
            TestCase(
                id="T004",
                input_news="国际贸易摩擦升级，出口企业承压",
                expected_sentiment=-0.5,
                expected_sectors=["出口", "贸易"],
                difficulty="medium"
            ),
            TestCase(
                id="T005",
                input_news="AI技术突破，但商业化落地仍需时间",
                expected_sentiment=0.1,
                expected_sectors=["AI", "科技"],
                difficulty="hard"  # 矛盾情感，需要 nuanced 理解
            ),
            TestCase(
                id="T006",
                input_news="公司发布超预期财报，但下调全年指引",
                expected_sentiment=-0.2,
                expected_sectors=["个股", "业绩"],
                difficulty="hard"  # 正负混合
            )
        ]
    
    def evaluate_sentiment_accuracy(self, expected: float, actual: float) -> float:
        """情感分数准确性：余弦相似度思路"""
        # 归一化到[-1,1]后计算误差
        diff = abs(expected - actual)
        # 误差<0.3给满分，误差>1给0分
        score = max(0, 1 - diff / 0.5)
        return round(score, 3)
    
    def evaluate_sector_recall(self, expected: List[str], actual: List[str]) -> float:
        """板块召回率：实际命中了多少期望板块"""
        if not expected:
            return 1.0
        expected_set = set(expected)
        actual_set = set(actual)
        hits = len(expected_set & actual_set)
        return round(hits / len(expected_set), 3)
    
    def evaluate_hallucination(self, raw_output: str, input_news: str) -> float:
        """幻觉检测：输出中是否包含输入未提及的信息
        
        简单实现：检查输出中的实体是否在输入中出现过
        实际业务中可用NER+知识图谱做更精确的检测
        """
        # 提取输入中的关键词（简单分词）
        input_words = set(re.findall(r'[\u4e00-\u9fa5]{2,}', input_news))
        
        # 提取输出中的声明性语句（包含数字、百分比、具体公司名称）
        claims = re.findall(r'[\u4e00-\u9fa5]+(?:增长|下跌|达到|预计|约为)\s*\d+(?:\.\d+)?%', raw_output)
        
        # 如果输出包含具体数字但输入没有，可能是幻觉
        hallucination_score = 0.0
        if claims and not re.search(r'\d+(?:\.\d+)?%', input_news):
            hallucination_score = 0.3  # 轻度幻觉
        
        return round(hallucination_score, 3)
    
    def run_evaluation(self, model_fn, model_name: str) -> Dict:
        """运行完整评测
        
        model_fn: 函数，接收news_title返回ModelResult
        """
        results = []
        total_latency = 0
        total_tokens = 0
        
        for tc in self.test_cases:
            # 调用模型
            start = time.time()
            result = model_fn(tc.input_news)
            latency = (time.time() - start) * 1000
            
            # 评分
            sentiment_acc = self.evaluate_sentiment_accuracy(tc.expected_sentiment, result.sentiment_score)
            sector_recall = self.evaluate_sector_recall(tc.expected_sectors, result.affected_sectors)
            hallucination = self.evaluate_hallucination(result.raw_output, tc.input_news)
            
            results.append({
                "case_id": tc.id,
                "difficulty": tc.difficulty,
                "sentiment_accuracy": sentiment_acc,
                "sector_recall": sector_recall,
                "hallucination": hallucination,
                "latency_ms": round(latency, 2),
                "token_usage": result.token_usage
            })
            
            total_latency += latency
            total_tokens += result.token_usage
        
        # 汇总统计
        df_results = [r for r in results]
        
        easy_scores = [r["sentiment_accuracy"] for r in df_results if r["difficulty"] == "easy"]
        hard_scores = [r["sentiment_accuracy"] for r in df_results if r["difficulty"] == "hard"]
        
        return {
            "model": model_name,
            "sentiment_accuracy": round(sum(r["sentiment_accuracy"] for r in df_results) / len(df_results), 3),
            "sector_recall": round(sum(r["sector_recall"] for r in df_results) / len(df_results), 3),
            "hallucination_rate": round(sum(r["hallucination"] for r in df_results) / len(df_results), 3),
            "avg_latency_ms": round(total_latency / len(df_results), 2),
            "avg_token_usage": round(total_tokens / len(df_results), 1),
            "easy_accuracy": round(sum(easy_scores) / len(easy_scores), 3) if easy_scores else 0,
            "hard_accuracy": round(sum(hard_scores) / len(hard_scores), 3) if hard_scores else 0,
            "details": df_results
        }
    
    def compare_models(self, results: List[Dict]) -> str:
        """生成对比报告"""
        lines = ["\n" + "=" * 60]
        lines.append("模型评测对比报告")
        lines.append("=" * 60)
        
        # 表头
        lines.append(f"{'模型':<15} {'情感准确率':<12} {'板块召回':<10} {'幻觉率':<10} {'延迟(ms)':<10} {'Token':<8}")
        lines.append("-" * 60)
        
        for r in results:
            lines.append(
                f"{r['model']:<15} "
                f"{r['sentiment_accuracy']:<12.3f} "
                f"{r['sector_recall']:<10.3f} "
                f"{r['hallucination_rate']:<10.3f} "
                f"{r['avg_latency_ms']:<10.1f} "
                f"{r['avg_token_usage']:<8.1f}"
            )
        
        lines.append("-" * 60)
        
        # 推荐
        best = min(results, key=lambda x: x["hallucination_rate"])
        lines.append(f"\n[推荐] {best['model']} — 幻觉率最低({best['hallucination_rate']:.3f})，适合金融场景")
        
        fastest = min(results, key=lambda x: x["avg_latency_ms"])
        lines.append(f"[最快] {fastest['model']} — 平均延迟{fastest['avg_latency_ms']:.1f}ms，适合实时场景")
        
        return "\n".join(lines)


# ========== 模拟模型实现 ==========

def mock_model_v1(news: str) -> ModelResult:
    """模拟模型v1：简单关键词匹配（基线）"""
    positive = ["利好", "增长", "降息", "突破", "超预期"]
    negative = ["下跌", "调查", "造假", "承压", "下调"]
    
    score = 0.0
    for p in positive:
        if p in news:
            score += 0.3
    for n in negative:
        if n in news:
            score -= 0.3
    
    score = max(-1, min(1, score))
    
    sectors = ["通用"]
    if "新能源" in news:
        sectors = ["新能源"]
    elif "金融" in news or "央行" in news:
        sectors = ["金融"]
    
    return ModelResult(
        sentiment_score=round(score, 2),
        affected_sectors=sectors,
        raw_output=f"情感: {score}",
        latency_ms=50,
        token_usage=100
    )

def mock_model_v2(news: str) -> ModelResult:
    """模拟模型v2：更 nuanced 的分析（升级版）"""
    # 更复杂的规则
    if "超预期" in news and "下调" in news:
        score = -0.2  # 混合情感，偏负面
    elif "降息" in news:
        score = 0.5
    elif "造假" in news or "调查" in news:
        score = -0.8
    elif "增长" in news:
        score = 0.8
    elif "AI" in news and "仍需时间" in news:
        score = 0.1  # 中性偏正面
    else:
        score = 0.0
    
    # 更精准的板块识别
    sectors = []
    if "新能源" in news:
        sectors.append("新能源")
    if "电池" in news or "宁德时代" in news:
        sectors.append("电池")
    if "金融" in news or "央行" in news:
        sectors.append("金融")
    if "AI" in news:
        sectors.append("AI")
    if not sectors:
        sectors = ["通用"]
    
    return ModelResult(
        sentiment_score=round(score, 2),
        affected_sectors=sectors,
        raw_output=f"分析: 情感{score}, 板块{sectors}",
        latency_ms=200,
        token_usage=500
    )

def mock_model_v3(news: str) -> ModelResult:
    """模拟模型v3：LLM-based（高幻觉风险）"""
    # 模拟LLM输出，有时过度解读
    if "超预期" in news:
        score = 0.7  # 过度乐观，忽略了"下调指引"
    elif "下调" in news:
        score = -0.3
    else:
        score = 0.0
    
    # 幻觉：可能生成输入中没有的信息
    raw = f"该新闻表明公司基本面{'强劲' if score > 0 else '疲软'}，预计下个季度营收增长15%"
    
    return ModelResult(
        sentiment_score=round(score, 2),
        affected_sectors=["个股", "业绩"],
        raw_output=raw,
        latency_ms=800,
        token_usage=2000
    )


if __name__ == "__main__":
    evaluator = ModelEvaluator()
    
    # 评测三个模型
    models = [
        (mock_model_v1, "Baseline_v1"),
        (mock_model_v2, "Enhanced_v2"),
        (mock_model_v3, "LLM_v3")
    ]
    
    results = []
    for model_fn, name in models:
        print(f"\n[评测] {name}...")
        result = evaluator.run_evaluation(model_fn, name)
        results.append(result)
        print(f"  情感准确率: {result['sentiment_accuracy']}")
        print(f"  板块召回率: {result['sector_recall']}")
        print(f"  幻觉率: {result['hallucination_rate']}")
    
    # 对比报告
    print(evaluator.compare_models(results))
