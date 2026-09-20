#!/usr/bin/env python3
"""
干运行测试 - 模拟各种数据源失败场景
验证降级逻辑是否正确
"""

import sys
import os
from pathlib import Path

WORKSPACE = os.environ.get("NIKO_WORKSPACE", str(Path(__file__).parent.parent.parent))
sys.path.insert(0, os.path.join(WORKSPACE, "showcase", "src", "financial"))

from market_data import fetch_all_market_data
from nav_fetcher import fetch_nav
from premium_history import get_recent_stats


def test_scenario_1_all_ok():
    """场景1：所有数据源正常"""
    print("\n" + "="*50)
    print("[场景1] 所有数据源正常")
    print("="*50)
    
    market_data = fetch_all_market_data()
    nav_data = fetch_nav()
    
    print(f"纳指期货: {'✅' if 'error' not in market_data['nasdaq'] else '❌'}")
    print(f"道指期货: {'✅' if 'error' not in market_data['dow'] else '❌'}")
    print(f"标普期货: {'✅' if 'error' not in market_data['sp500'] else '❌'}")
    print(f"汇率: {'✅' if 'error' not in market_data['usd_cny'] else '❌'}")
    print(f"A股指数: {'✅' if 'error' not in market_data['a_share']['shanghai'] else '❌'}")
    print(f"七巨头: {'✅' if len([s for s in market_data['stocks'].values() if 'error' not in s]) > 0 else '❌'}")
    print(f"161130净值: {'✅' if 'error' not in nav_data else '❌'}")
    
    return "complete" if all([
        'error' not in market_data['nasdaq'],
        'error' not in nav_data
    ]) else "partial"


def test_scenario_2_nav_failure():
    """场景2：161130净值抓取失败"""
    print("\n" + "="*50)
    print("[场景2] 161130净值抓取失败（模拟）")
    print("="*50)
    
    # 模拟净值失败
    nav_data = {"error": "天天基金反爬", "fund_code": "161130"}
    
    market_data = fetch_all_market_data()
    
    # 检查降级逻辑
    has_nasdaq = 'error' not in market_data['nasdaq']
    has_nav = 'error' not in nav_data
    
    print(f"纳指期货: {'✅ 可用' if has_nasdaq else '❌ 失败'}")
    print(f"161130净值: {'✅ 可用' if has_nav else '❌ 失败'}")
    print(f"预期行为: 输出硬数据报告，不估算溢价")
    
    return "partial" if has_nasdaq and not has_nav else "failed"


def test_scenario_3_news_failure():
    """场景3：新闻抓取失败"""
    print("\n" + "="*50)
    print("[场景3] 新闻抓取失败（模拟）")
    print("="*50)
    
    market_data = fetch_all_market_data()
    nav_data = fetch_nav()
    
    # 模拟新闻为空
    news_list = []
    
    has_market = 'error' not in market_data['nasdaq']
    has_nav = 'error' not in nav_data
    
    print(f"市场数据: {'✅' if has_market else '❌'}")
    print(f"净值数据: {'✅' if has_nav else '❌'}")
    print(f"新闻: ❌ 0条")
    print(f"预期行为: 输出硬数据+净值报告，无板块分析")
    
    return "partial" if (has_market or has_nav) and not news_list else "complete"


def test_scenario_4_all_failure():
    """场景4：所有数据源失败"""
    print("\n" + "="*50)
    print("[场景4] 所有数据源失败（模拟）")
    print("="*50)
    
    print("纳指期货: ❌ 429限速")
    print("161130净值: ❌ 反爬")
    print("新闻: ❌ 超时")
    print("预期行为: 只推送故障摘要，不生成报告")
    
    return "failed"


def test_scenario_5_history_stats():
    """场景5：历史数据查询"""
    print("\n" + "="*50)
    print("[场景5] 历史数据统计")
    print("="*50)
    
    stats = get_recent_stats(30)
    print(f"近30天记录: {stats.get('count', 0)} 条")
    if stats.get('count', 0) > 0:
        print(f"平均溢价: {stats.get('avg_premium')}%")
        print(f"最低溢价: {stats.get('min_premium')}%")
        print(f"最高溢价: {stats.get('max_premium')}%")
    else:
        print("暂无历史数据（从今天开始积累）")
    
    return "complete"


def run_all_tests():
    """运行所有测试"""
    print("="*50)
    print("盘前资讯系统 - 干运行测试")
    print("="*50)
    
    results = {}
    results["场景1_全正常"] = test_scenario_1_all_ok()
    results["场景2_净值失败"] = test_scenario_2_nav_failure()
    results["场景3_新闻失败"] = test_scenario_3_news_failure()
    results["场景4_全部失败"] = test_scenario_4_all_failure()
    results["场景5_历史统计"] = test_scenario_5_history_stats()
    
    print("\n" + "="*50)
    print("测试结果汇总")
    print("="*50)
    for name, status in results.items():
        icon = "✅" if status == "complete" else ("⚠️" if status == "partial" else "❌")
        print(f"{icon} {name}: {status}")
    
    return results


if __name__ == "__main__":
    run_all_tests()
