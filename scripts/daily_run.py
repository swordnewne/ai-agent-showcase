# -*- coding: utf-8 -*-
"""
每日定时执行脚本
用法:
    python scripts/daily_run.py --mode premarket   # 盘前分析
    python scripts/daily_run.py --mode postmarket  # 盘后复盘

接入cron定时执行:
    30 8 * * 1-5  python /path/to/daily_run.py --mode premarket
    30 15 * * 1-5 python /path/to/daily_run.py --mode postmarket
"""

import sys
import os
import argparse
import logging

# 将项目根目录加入路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from showcase.src.financial.launcher import get_launcher
from showcase.src.financial.report_generator import get_generator

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
logger = logging.getLogger("daily_run")


def main():
    parser = argparse.ArgumentParser(description="股票交易系统每日执行")
    parser.add_argument(
        "--mode",
        choices=["premarket", "postmarket", "test"],
        required=True,
        help="执行模式: premarket=盘前分析, postmarket=盘后复盘, test=测试"
    )
    parser.add_argument(
        "--output",
        choices=["console", "wechat"],
        default="console",
        help="输出目标: console=打印到控制台, wechat=推送到企业微信"
    )
    parser.add_argument(
        "--stock",
        help="指定分析单只股票（测试模式用）"
    )
    
    args = parser.parse_args()
    launcher = get_launcher()
    
    if args.mode == "premarket":
        logger.info("执行盘前分析...")
        report = launcher.cmd_premarket()
        
        if args.output == "console":
            print(report)
        elif args.output == "wechat":
            # TODO: 接入企业微信推送
            logger.info("[WECHAT] 推送盘前报告 (长度: %d)", len(report))
            print(report)
    
    elif args.mode == "postmarket":
        logger.info("执行盘后复盘...")
        report = launcher.cmd_postmarket()
        
        if args.output == "console":
            print(report)
        elif args.output == "wechat":
            logger.info("[WECHAT] 推送盘后报告 (长度: %d)", len(report))
            print(report)
    
    elif args.mode == "test":
        logger.info("执行测试模式...")
        if args.stock:
            # 测试单只股票分析
            print("测试单股分析: {}".format(args.stock))
            # TODO: 实现单股测试
        else:
            # 测试完整流程
            print("=" * 40)
            print("测试盘前分析")
            print("=" * 40)
            report = launcher.cmd_premarket()
            print(report)
            
            print("\n")
            print("=" * 40)
            print("测试盘后复盘")
            print("=" * 40)
            report = launcher.cmd_postmarket()
            print(report)


if __name__ == "__main__":
    main()
