#!/usr/bin/env python3
"""
AI金融信号系统 - 新闻舆情分析模块
- 双轨制：早盘全自动(8:30) + 盘中预警(9:30-15:00)
- 凯利公式仓位管理：半凯利保守策略
- 13层优化→0人工审核
"""
import json, os
from datetime import datetime

# ========== 凯利公式参数 ==========
KELLY_CONFIG = {
    "win_rate": 0.6,       # 胜率估计
    "profit_ratio": 2.0,   # 盈亏比
    "fraction": 0.5,       # 半凯利（保守）
    "take_profit": 0.20,   # 止盈20%
    "stop_loss": -0.10,    # 止损10%
}

def kelly_position(win_rate: float, profit_ratio: float) -> float:
    """凯利公式计算最优仓位
    
    f = (p*b - q) / b
    其中 p=胜率, q=1-p, b=盈亏比
    
    半凯利策略：f/2（降低风险，提高稳健性）
    """
    q = 1 - win_rate
    f = (win_rate * profit_ratio - q) / profit_ratio
    return f * KELLY_CONFIG["fraction"]  # 半凯利

def build_news_prompt(news_list: list) -> str:
    """构建新闻分析Prompt
    
    核心设计：给DeepSeek结构化输入，要求结构化输出。
    避免"分析一下今天的新闻"这种模糊指令。
    """
    news_text = "\n".join([
        f"{i+1}. [{n.get('time', '')}] {n.get('title', '')}"
        for i, n in enumerate(news_list[:20])  # 限制20条，防止token超限
    ])
    
    return f"""你是一位量化交易分析师。基于以下新闻，分析市场情绪并给出交易信号。

## 新闻列表
{news_text}

## 要求输出（JSON格式）
{{
    "sentiment_score": -1.0到1.0的浮点数,
    "affected_sectors": ["受影响的板块"],
    "position_ratio": 0到100的整数（建议仓位百分比）,
    "top_picks": ["推荐关注的股票代码"],
    "risks": ["潜在风险点"],
    "take_profit": "建议止盈点位",
    "stop_loss": "建议止损点位"
}}

规则：
- 正面新闻+0.3，负面新闻-0.3，重大政策±0.5
- 涉及持仓股的负面新闻权重翻倍
- 没有明确方向时，sentiment_score给0.0，position_ratio给50
"""

def parse_signal(llm_output: str) -> dict:
    """解析LLM输出为结构化信号
    
    容错设计：LLM可能输出markdown代码块，需要清洗。
    """
    # 清洗markdown代码块
    text = llm_output.strip()
    if text.startswith("```json"):
        text = text[7:]
    if text.endswith("```"):
        text = text[:-3]
    text = text.strip()
    
    try:
        signal = json.loads(text)
    except json.JSONDecodeError:
        # 降级：尝试正则提取
        signal = {
            "sentiment_score": 0.0,
            "position_ratio": 50,
            "note": "解析失败，使用默认信号"
        }
    
    return signal

def calculate_shares(cash: float, stock_price: float, position_ratio: float) -> int:
    """计算应买入股数（整数股）
    
    关键修复：order_value不可靠（资金不足时无法成交），
    改为order(股数)+3重价格兜底。
    """
    target_value = cash * (position_ratio / 100)
    shares = int(target_value / stock_price / 100) * 100  # 整手（100股倍数）
    
    # 兜底：确保至少1手
    if shares == 0 and target_value > stock_price * 100:
        shares = 100
    
    return shares

def run_morning_analysis(news_list: list, portfolio: dict) -> dict:
    """早盘分析流程（8:30执行）"""
    # 1. 构建prompt
    prompt = build_news_prompt(news_list)
    
    # 2. 调用LLM（实际代码调用DeepSeek API）
    # llm_output = deepseek.chat(prompt)
    
    # 3. 解析信号（这里用mock演示）
    signal = {
        "sentiment_score": 0.3,
        "position_ratio": 70,
        "top_picks": ["600519", "300750"],  # 茅台/宁德时代
        "take_profit": "+20%",
        "stop_loss": "-10%"
    }
    
    # 4. 计算仓位
    kelly = kelly_position(0.6, 2.0)  # 半凯利=25%仓位
    adjusted_position = min(signal["position_ratio"] / 100, kelly)
    
    print(f"[早盘分析] 情绪分:{signal['sentiment_score']} 仓位:{adjusted_position:.1%}")
    print(f"[凯利公式] 半凯利={kelly:.1%}")
    
    return {
        "signal": signal,
        "adjusted_position": adjusted_position,
        "kelly_raw": kelly
    }

if __name__ == "__main__":
    # 演示：模拟新闻列表
    demo_news = [
        {"time": "08:00", "title": "央行宣布降息25bp，市场流动性充裕"},
        {"time": "08:15", "title": "新能源板块政策利好，宁德时代订单增长"},
    ]
    
    result = run_morning_analysis(demo_news, portfolio={})
    print(f"\n{json.dumps(result, ensure_ascii=False, indent=2)}")
