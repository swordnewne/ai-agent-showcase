#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
持仓数据导入工具
支持格式：
1. 聚宽CSV导出（交易记录）
2. 自定义JSON（持仓快照）
3. 手动录入（交互式）

用法：
    python3 import_portfolio.py --format joinquant --file trades.csv
    python3 import_portfolio.py --format json --file positions.json
    python3 import_portfolio.py --format manual  # 交互式录入

Python 3.6+ 兼容
"""

import argparse
import csv
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from typing import Dict, List, Optional

# 数据库路径
DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "finance.db"
)


def get_db():
    """获取数据库连接"""
    db_path = os.path.abspath(DB_PATH)
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """初始化数据库表结构"""
    schema_path = os.path.join(os.path.dirname(__file__), "..", "data", "db_schema.sql")
    if not os.path.exists(schema_path):
        print("错误：找不到数据库schema文件: {}".format(schema_path))
        return False
    
    conn = get_db()
    with open(schema_path, "r") as f:
        conn.executescript(f.read())
    conn.commit()
    conn.close()
    print("数据库表结构已初始化")
    return True


def parse_joinquant_csv(filepath: str) -> List[Dict]:
    """
    解析聚宽交易记录CSV
    
    聚宽CSV格式（通常包含）：
    - 成交时间, 股票代码, 股票名称, 操作(买/卖), 成交数量, 成交价格
    """
    events = []
    
    with open(filepath, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # 尝试多种可能的列名
            event = {
                "trade_time": row.get("成交时间") or row.get("时间") or row.get("date"),
                "symbol": row.get("股票代码") or row.get("代码") or row.get("symbol"),
                "name": row.get("股票名称") or row.get("名称") or row.get("name"),
                "action": row.get("操作") or row.get("action") or row.get("type"),
                "quantity": row.get("成交数量") or row.get("数量") or row.get("quantity"),
                "price": row.get("成交价格") or row.get("价格") or row.get("price"),
            }
            
            # 标准化action
            action_raw = str(event["action"] or "").strip().lower()
            if action_raw in ("买", "买入", "buy", "b"):
                event["action"] = "buy"
            elif action_raw in ("卖", "卖出", "sell", "s"):
                event["action"] = "sell"
            else:
                continue  # 跳过未知操作
            
            # 解析数值
            try:
                event["quantity"] = int(float(str(event["quantity"]).replace(",", "")))
                event["price"] = float(str(event["price"]).replace(",", ""))
            except (ValueError, TypeError):
                continue
            
            # 解析时间
            try:
                dt = datetime.strptime(event["trade_time"], "%Y-%m-%d %H:%M:%S")
                event["trade_time"] = dt.strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                try:
                    dt = datetime.strptime(event["trade_time"], "%Y-%m-%d")
                    event["trade_time"] = dt.strftime("%Y-%m-%d 14:30:00")
                except Exception:
                    event["trade_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            # 标准化symbol
            symbol = str(event["symbol"] or "").strip()
            if "." not in symbol:
                # 添加后缀
                if symbol.startswith("6"):
                    symbol = symbol + ".SH"
                else:
                    symbol = symbol + ".SZ"
            event["symbol"] = symbol
            
            events.append(event)
    
    return events


def parse_json_positions(filepath: str) -> List[Dict]:
    """解析JSON格式的持仓数据"""
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    events = []
    
    # 支持两种JSON格式
    # 格式1: { "positions": [{ "symbol": "600519.SH", "quantity": 100, "avg_cost": 1000 }] }
    # 格式2: [{ "symbol": "600519.SH", "action": "buy", "quantity": 100, "price": 1000, "time": "2024-01-01" }]
    
    if isinstance(data, dict) and "positions" in data:
        # 格式1: 持仓快照
        for pos in data["positions"]:
            event = {
                "trade_time": pos.get("time", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
                "symbol": pos["symbol"],
                "name": pos.get("name", ""),
                "action": pos.get("action", "buy"),
                "quantity": int(pos["quantity"]),
                "price": float(pos.get("avg_cost", pos.get("price", 0))),
            }
            events.append(event)
    elif isinstance(data, list):
        # 格式2: 交易记录列表
        for item in data:
            event = {
                "trade_time": item.get("time", item.get("trade_time", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))),
                "symbol": item["symbol"],
                "name": item.get("name", ""),
                "action": item.get("action", "buy"),
                "quantity": int(item["quantity"]),
                "price": float(item.get("price", item.get("avg_cost", 0))),
            }
            events.append(event)
    
    return events


def import_events(events: List[Dict], dry_run: bool = False):
    """导入交易记录到数据库"""
    if not events:
        print("没有交易记录可导入")
        return
    
    print("共 {} 条交易记录".format(len(events)))
    
    if dry_run:
        print("\n[试运行模式，不写入数据库]")
        for e in events[:5]:
            print("  {} {} {} {}股 @ {}".format(
                e["trade_time"], e["symbol"], e["action"], e["quantity"], e["price"]
            ))
        if len(events) > 5:
            print("  ... 还有 {} 条".format(len(events) - 5))
        return
    
    conn = get_db()
    cursor = conn.cursor()
    
    imported = 0
    for e in events:
        try:
            cursor.execute("""
                INSERT INTO sig_portfolio_events
                (event_type, event_date, symbol, side, quantity, price, note)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                "trade",
                e["trade_time"].split()[0] if " " in e["trade_time"] else e["trade_time"],
                e["symbol"],
                e["action"],
                e["quantity"],
                e["price"],
                e.get("name", "")
            ))
            imported += 1
        except Exception as ex:
            print("导入失败 {}: {}".format(e["symbol"], ex))
    
    conn.commit()
    conn.close()
    print("成功导入 {} 条记录".format(imported))


def interactive_input():
    """交互式录入持仓"""
    print("=" * 40)
    print("交互式持仓录入")
    print("格式: 代码 名称 持仓数量 成本价")
    print("示例: 600519 贵州茅台 100 1000.50")
    print("输入 'done' 结束，输入 'skip' 跳过")
    print("=" * 40)
    
    events = []
    while True:
        line = input("> ").strip()
        if line.lower() in ("done", "exit", "quit"):
            break
        if line.lower() == "skip":
            return []
        if not line:
            continue
        
        parts = line.split()
        if len(parts) < 4:
            print("格式错误，请按: 代码 名称 数量 成本价")
            continue
        
        try:
            code = parts[0]
            name = parts[1]
            quantity = int(parts[2])
            price = float(parts[3])
            
            # 添加后缀
            if "." not in code:
                if code.startswith("6"):
                    code = code + ".SH"
                else:
                    code = code + ".SZ"
            
            events.append({
                "trade_time": datetime.now().strftime("%Y-%m-%d 09:30:00"),
                "symbol": code,
                "name": name,
                "action": "buy",
                "quantity": quantity,
                "price": price,
            })
            print("  已添加: {} {} {}股 @ {}".format(code, name, quantity, price))
        except Exception as e:
            print("解析错误: {}".format(e))
    
    return events


def generate_sample_portfolio():
    """
    生成示例持仓（基于用户历史股票池）
    用于快速初始化测试
    """
    # 用户的20只股票池
    stocks = [
        ("600519.SH", "贵州茅台", 100, 1000.0),
        ("300750.SZ", "宁德时代", 200, 200.0),
        ("300308.SZ", "中际旭创", 300, 100.0),
        ("600030.SH", "中信证券", 500, 25.0),
        ("000333.SZ", "美的集团", 400, 50.0),
        ("002475.SZ", "立讯精密", 600, 30.0),
        ("600276.SH", "恒瑞医药", 300, 40.0),
        ("300418.SZ", "昆仑万维", 400, 35.0),
        ("002594.SZ", "比亚迪", 200, 200.0),
        ("000858.SZ", "五粮液", 300, 150.0),
        ("000001.SZ", "平安银行", 1000, 10.0),
        ("600887.SH", "伊利股份", 500, 25.0),
        ("002230.SZ", "科大讯飞", 400, 45.0),
        ("300014.SZ", "亿纬锂能", 300, 60.0),
        ("601012.SH", "隆基绿能", 600, 20.0),
        ("300760.SZ", "迈瑞医疗", 100, 300.0),
        ("601899.SH", "紫金矿业", 800, 12.0),
        ("000725.SZ", "京东方A", 2000, 4.0),
        ("603501.SH", "韦尔股份", 200, 100.0),
        ("600809.SH", "山西汾酒", 200, 200.0),
    ]
    
    events = []
    for code, name, qty, price in stocks:
        events.append({
            "trade_time": "2024-01-02 09:30:00",  # 模拟年初建仓
            "symbol": code,
            "name": name,
            "action": "buy",
            "quantity": qty,
            "price": price,
        })
    
    return events


def main():
    parser = argparse.ArgumentParser(description="持仓数据导入工具")
    parser.add_argument("--format", choices=["joinquant", "json", "manual", "sample"],
                        default="sample",
                        help="导入格式")
    parser.add_argument("--file", help="数据文件路径")
    parser.add_argument("--dry-run", action="store_true",
                        help="试运行，不写入数据库")
    parser.add_argument("--init-db", action="store_true",
                        help="初始化数据库表结构")
    
    args = parser.parse_args()
    
    # 初始化数据库
    if args.init_db:
        init_db()
        return
    
    # 解析数据
    if args.format == "joinquant":
        if not args.file:
            print("错误：--format joinquant 需要 --file")
            return
        events = parse_joinquant_csv(args.file)
    elif args.format == "json":
        if not args.file:
            print("错误：--format json 需要 --file")
            return
        events = parse_json_positions(args.file)
    elif args.format == "manual":
        events = interactive_input()
    else:  # sample
        print("使用示例持仓数据（20只股票，模拟年初建仓）")
        events = generate_sample_portfolio()
    
    # 导入
    import_events(events, dry_run=args.dry_run)
    
    if not args.dry_run and events:
        print("\n提示：现在可以运行 launcher.cmd_portfolio() 查看持仓日报")


if __name__ == "__main__":
    main()
