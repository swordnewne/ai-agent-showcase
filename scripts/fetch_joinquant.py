#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
聚宽模拟交易持仓自动抓取工具（HTML解析版）

用法：
    python3 fetch_joinquant.py --backtest-id xxx --import-db

Python 3.6+ 兼容
"""

import argparse
import json
import os
import re
import sqlite3
import sys
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "finance.db")


def get_credentials():
    """获取聚宽账号密码"""
    username = os.environ.get("JQ_USERNAME")
    password = os.environ.get("JQ_PASSWORD")
    
    if not username or not password:
        cred_file = os.path.expanduser("~/.joinquant_credentials.json")
        if os.path.exists(cred_file):
            with open(cred_file, "r") as f:
                creds = json.load(f)
                username = creds.get("username")
                password = creds.get("password")
    
    if not username or not password:
        print("错误：缺少聚宽账号密码")
        print("请设置环境变量 JQ_USERNAME 和 JQ_PASSWORD")
        return None, None
    
    return username, password


def login_joinquant(username: str, password: str, fetch_logs: bool = False) -> dict:
    """
    使用Playwright登录聚宽并获取持仓数据
    
    Args:
        fetch_logs: 是否同时抓取交易日志
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("错误：未安装 Playwright")
        print("运行: pip3 install playwright --break-system-packages")
        return None
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1280, "height": 800})
        page = context.new_page()
        
        try:
            # 1. 访问登录页面
            print("正在访问聚宽登录页面...")
            page.goto("https://www.joinquant.com/user/login/index", 
                      wait_until="networkidle", timeout=30000)
            page.wait_for_load_state("networkidle", timeout=15000)
            
            # 2. 填写登录表单（等待元素加载）
            print("正在登录...")
            
            # 等待用户名输入框
            page.wait_for_selector("input[name='username']", timeout=15000)
            page.fill("input[name='username']", username)
            print("  填写用户名")
            
            # 等待密码输入框
            page.wait_for_selector("input[type='password']", timeout=15000)
            page.fill("input[type='password']", password)
            print("  填写密码")
            
            # 勾选协议
            try:
                page.click("input[type='checkbox']")
                print("  勾选协议")
            except:
                pass
            
            # 点击登录
            page.click("button:has-text('登 录')")
            page.wait_for_timeout(3000)
            
            # 检查登录成功
            login_success = False
            for sel in ["text=退出", "text=我的策略", "[class*='user-info']"]:
                try:
                    if page.is_visible(sel, timeout=3000):
                        login_success = True
                        print(f"  登录成功指示器: {sel}")
                        break
                except:
                    pass
            
            if not login_success:
                print("登录失败")
                page.screenshot(path="/tmp/jq_login_failed.png")
                return None
            
            print("登录成功！")
            
            # 3. 访问模拟交易页面
            backtest_id = os.environ.get("JQ_BACKTEST_ID", "")
            if not backtest_id:
                print("错误：缺少 JQ_BACKTEST_ID")
                return None
            
            print(f"正在获取持仓数据...")
            page.goto(
                f"https://www.joinquant.com/algorithm/live/index?backtestId={backtest_id}",
                wait_until="networkidle", timeout=30000
            )
            page.wait_for_load_state("networkidle", timeout=15000)
            page.wait_for_timeout(3000)
            
            # 4. 提取持仓数据
            html = page.content()
            
            # 保存HTML用于调试
            with open("/tmp/jq_page.html", "w", encoding="utf-8") as f:
                f.write(html)
            
            holdings = []
            
            # 方法1：从 title="名称(代码.XSHG/XSHE)" 提取
            title_pattern = r'title="([^"]+)\((\d{6})\.(XSHG|XSHE)\)"'
            title_matches = re.findall(title_pattern, html)
            
            if title_matches:
                print(f"从title属性找到 {len(title_matches)} 只股票")
                for name, code, exchange in title_matches:
                    # 查找对应的数量（在代码后500字符内搜索）
                    code_pos = html.find(f"{code}.{exchange}")
                    if code_pos > 0:
                        snippet = html[code_pos:code_pos+500]
                        
                        # 搜索数量：title="XXX股"
                        qty_match = re.search(r'title="(\d+)股"', snippet)
                        quantity = qty_match.group(1) if qty_match else "0"
                        
                        # 搜索成本：title="价格"
                        cost_match = re.search(r'title="([\d.]+)"', snippet)
                        cost = cost_match.group(1) if cost_match else "0"
                    else:
                        quantity = "0"
                        cost = "0"
                    
                    holdings.append({
                        "code": code,
                        "name": name,
                        "quantity": quantity,
                        "cost": cost,
                    })
            
            # 方法2：从表格数据提取（如果方法1没找到数量）
            if not holdings or all(h["quantity"] == "0" for h in holdings):
                print("尝试从表格数据提取...")
                # 查找所有表格行数据
                row_pattern = r'<tr[^>]*>.*?\b(\d{6})\b.*?([\u4e00-\u9fa5]+).*?(\d+).*?([\d.]+).*?</tr>'
                row_matches = re.findall(row_pattern, html, re.DOTALL)
                for code, name, qty, cost in row_matches:
                    if not any(h["code"] == code for h in holdings):
                        holdings.append({
                            "code": code,
                            "name": name,
                            "quantity": qty,
                            "cost": cost,
                        })
            
            print(f"提取到 {len(holdings)} 条持仓记录")
            
            # 提取总资产和现金
            total_equity = 0
            cash = 0
            
            total_match = re.search(r'总资产[：:]\s*([\d,]+\.?\d*)', html)
            if total_match:
                total_equity = float(total_match.group(1).replace(',', ''))
            
            cash_match = re.search(r'可用资金[：:]\s*([\d,]+\.?\d*)', html)
            if cash_match:
                cash = float(cash_match.group(1).replace(',', ''))
            
            result = {
                "holdings": holdings,
                "cash": cash,
                "total_equity": total_equity,
                "backtest_id": backtest_id,
            }
            
            # 抓取交易日志（可选）
            if fetch_logs:
                print("\n正在抓取交易日志...")
                print("  （日志加载较慢，等待15秒...）")
                try:
                    # 访问日志页面（持仓页面内的日志tab）
                    # 先回到持仓页面，点击日志tab
                    page.goto(
                        f"https://www.joinquant.com/algorithm/live/index?backtestId={backtest_id}",
                        wait_until="networkidle", timeout=30000
                    )
                    page.wait_for_load_state("networkidle", timeout=15000)
                    
                    # 点击日志tab（如果存在）
                    log_tab_selectors = [
                        "a:has-text('日志')",
                        "a:has-text('trade')",
                        "[href*='#pane_logs']",
                        "text=日志输出",
                    ]
                    for sel in log_tab_selectors:
                        try:
                            if page.is_visible(sel, timeout=3000):
                                page.click(sel)
                                print(f"  点击日志tab: {sel}")
                                break
                        except:
                            pass
                    
                    # 等待日志加载（最长15秒）
                    # 检测"正在加载日志..."是否消失
                    max_wait = 15
                    for i in range(max_wait):
                        page.wait_for_timeout(1000)  # 每秒检查一次
                        html = page.content()
                        if "正在加载日志" not in html and "loading_s" not in html:
                            print(f"  日志加载完成（等待{i+1}秒）")
                            break
                        print(f"  等待日志加载... ({i+1}/{max_wait})")
                    
                    log_html = page.content()
                    
                    # 保存日志HTML用于调试
                    with open("/tmp/jq_log_page.html", "w", encoding="utf-8") as f:
                        f.write(log_html)
                    print("  日志页面HTML已保存到 /tmp/jq_log_page.html")
                    
                    # 尝试提取交易记录
                    trades = []
                    
                    # 方法1：从表格行提取
                    trade_patterns = [
                        # 格式：2024-01-15 300308 中际旭创 买入 100 125.50
                        r'<tr[^>]*>.*?([\d-]{4,10}).*?(\d{6}).*?([\u4e00-\u9fa5]+).*?(买入|卖出|买|卖).*?(\d+).*?([\d.]+).*?</tr>',
                        # 更宽松的格式
                        r'<tr[^>]*>.*?([\d/\-]{4,10}).*?(\d{6}).*?(买入|卖出|买|卖).*?(\d+).*?([\d.]+).*?</tr>',
                    ]
                    
                    for pattern in trade_patterns:
                        matches = re.findall(pattern, log_html, re.DOTALL)
                        if matches:
                            for m in matches:
                                if len(m) >= 5:
                                    trades.append({
                                        "date": m[0],
                                        "code": m[1] if len(m) > 1 else "",
                                        "name": m[2] if len(m) > 2 and not m[2] in ["买入", "卖出", "买", "卖"] else "",
                                        "side": m[3] if m[3] in ["买入", "卖出", "买", "卖"] else (m[2] if m[2] in ["买入", "卖出", "买", "卖"] else "买入"),
                                        "quantity": m[-2] if m[-2].isdigit() else m[-3],
                                        "price": m[-1],
                                    })
                            break
                    
                    # 方法2：从页面文本提取日期+代码+买卖模式
                    if not trades:
                        text_pattern = r'([\d-]{4,10})\s+(\d{6})\s+.*?([\u4e00-\u9fa5]+)?\s*(买入|卖出|买|卖)\s+(\d+)\s*@?\s*([\d.]+)'
                        text_matches = re.findall(text_pattern, log_html)
                        for m in text_matches:
                            trades.append({
                                "date": m[0],
                                "code": m[1],
                                "name": m[2] if m[2] else "",
                                "side": m[3],
                                "quantity": m[4],
                                "price": m[5],
                            })
                    
                    # 方法3：从<pre>标签提取（日志输出格式）
                    if not trades:
                        pre_blocks = re.findall(r'<pre[^>]*>([\s\S]*?)</pre>', log_html)
                        for pre in pre_blocks:
                            lines = pre.strip().split('\n')
                            for line in lines:
                                # 匹配: 2024-01-15 09:30:00 买入 300308 中际旭创 100股 @ 125.50
                                log_match = re.search(r'([\d-]{4,10}).*?(买入|卖出|买|卖)\s+(\d{6}).*?(\d+)\s*股?\s*@?\s*([\d.]+)', line)
                                if log_match:
                                    trades.append({
                                        "date": log_match.group(1),
                                        "code": log_match.group(3),
                                        "side": log_match.group(2),
                                        "quantity": log_match.group(4),
                                        "price": log_match.group(5),
                                        "name": "",
                                    })
                    
                    if trades:
                        print(f"提取到 {len(trades)} 条交易日志")
                        result["trades"] = trades
                    else:
                        print("未提取到交易日志（页面可能为空或格式不匹配）")
                        result["trades"] = []
                        
                except Exception as e:
                    print(f"抓取日志失败: {e}")
                    result["trades"] = []
            
            return result
            
        finally:
            browser.close()


def save_to_db(data: dict):
    """保存持仓数据和交易日志到数据库"""
    if not data:
        print("没有数据可保存")
        return
    
    db_path = os.path.abspath(DB_PATH)
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # 保存持仓
    holdings_imported = 0
    for h in data.get("holdings", []):
        try:
            code = h.get("code", "")
            if not code:
                continue
            
            if "." not in code:
                if code.startswith("6"):
                    code = code + ".SH"
                else:
                    code = code + ".SZ"
            
            quantity = int(h.get("quantity", 0))
            if quantity <= 0:
                continue
            
            cost = float(h.get("cost", 0))
            if cost <= 0:
                cost = 100.0
            
            cursor.execute("""
                INSERT OR REPLACE INTO portfolio_events
                (event_type, event_date, symbol, side, quantity, price, note)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                "trade",
                datetime.now().strftime("%Y-%m-%d"),
                code,
                "buy",
                quantity,
                cost,
                h.get("name", "")
            ))
            holdings_imported += 1
        except Exception as e:
            print(f"导入持仓失败: {e}")
    
    # 保存交易日志
    trades_imported = 0
    for t in data.get("trades", []):
        try:
            code = t.get("code", "")
            if not code:
                continue
            
            if "." not in code:
                if code.startswith("6"):
                    code = code + ".SH"
                else:
                    code = code + ".SZ"
            
            quantity = int(t.get("quantity", 0))
            if quantity <= 0:
                continue
            
            price = float(t.get("price", 0))
            if price <= 0:
                price = 100.0
            
            side = "buy" if t.get("side", "") in ["买入", "买"] else "sell"
            
            # 使用交易日期，如果没有则用今天
            trade_date = t.get("date", "")
            if not trade_date or len(trade_date) < 8:
                trade_date = datetime.now().strftime("%Y-%m-%d")
            # 统一格式为 YYYY-MM-DD
            trade_date = trade_date.replace("/", "-").replace(".", "-")
            
            cursor.execute("""
                INSERT OR REPLACE INTO portfolio_events
                (event_type, event_date, symbol, side, quantity, price, note)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                "trade",
                trade_date,
                code,
                side,
                quantity,
                price,
                t.get("name", "")
            ))
            trades_imported += 1
        except Exception as e:
            print(f"导入交易日志失败: {e}")
    
    conn.commit()
    conn.close()
    
    if holdings_imported > 0:
        print(f"成功导入 {holdings_imported} 条持仓记录")
    if trades_imported > 0:
        print(f"成功导入 {trades_imported} 条交易日志")
    if holdings_imported == 0 and trades_imported == 0:
        print("没有新数据导入")


def main():
    parser = argparse.ArgumentParser(description="聚宽持仓自动抓取")
    parser.add_argument("--backtest-id", help="回测ID")
    parser.add_argument("--import-db", action="store_true", help="导入数据库")
    parser.add_argument("--fetch-logs", action="store_true", help="同时抓取交易日志")
    
    args = parser.parse_args()
    
    if args.backtest_id:
        os.environ["JQ_BACKTEST_ID"] = args.backtest_id
    
    username, password = get_credentials()
    if not username:
        return 1
    
    data = login_joinquant(username, password, fetch_logs=args.fetch_logs)
    if not data:
        return 1
    
    print(json.dumps(data, ensure_ascii=False, indent=2))
    
    if args.import_db:
        save_to_db(data)
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
