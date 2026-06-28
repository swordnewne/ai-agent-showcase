#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
聚宽模拟交易持仓抓取（Cookie版，跳过登录）
"""
import json
import os
import re
import sqlite3
import sys
from datetime import datetime

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("错误：未安装 Playwright")
    sys.exit(1)

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "finance.db")

# Cookie数据（从浏览器导出）
COOKIES = [
    {"name": "uid", "value": "wKgyrWohTYl46gW0mPsjAg==", "domain": "www.joinquant.com", "path": "/"},
    {"name": "token", "value": "4534895bbc416603f7aa6bf5c6d96fc4e3b11b26", "domain": "www.joinquant.com", "path": "/"},
    {"name": "tips", "value": "1", "domain": "www.joinquant.com", "path": "/"},
    {"name": "isFirst", "value": "0", "domain": "www.joinquant.com", "path": "/algorithm/index"},
    {"name": "getStrategy", "value": "1", "domain": "www.joinquant.com", "path": "/"},
    {"name": "PHPSESSID", "value": "tb0o5l0ldkjhkuoa7t07hc8t70", "domain": "www.joinquant.com", "path": "/"},
]


def fetch_with_cookies(backtest_id, fetch_logs=False):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1280, "height": 800})
        
        # 加载Cookie
        for cookie in COOKIES:
            try:
                context.add_cookies([cookie])
            except Exception as e:
                print(f"  Cookie加载警告: {cookie['name']} - {e}")
        
        page = context.new_page()
        print(f"Cookie已加载，正在访问持仓页面...")
        
        # 访问持仓页面
        page.goto(
            f"https://www.joinquant.com/algorithm/live/index?backtestId={backtest_id}",
            wait_until="networkidle", timeout=30000
        )
        page.wait_for_timeout(3000)
        
        html = page.content()
        with open("/tmp/jq_page_cookie.html", "w", encoding="utf-8") as f:
            f.write(html)
        
        # 检查是否登录成功
        if "login" in page.url or "登录" in html[:1000]:
            print("⚠️ Cookie可能已过期，页面重定向到登录")
            page.screenshot(path="/tmp/jq_cookie_failed.png")
            return None
        
        print(f"页面已加载: {page.url}")
        
        # 提取持仓
        holdings = []
        title_pattern = r'title="([^"]+)\((\d{6})\.(XSHG|XSHE)\)"'
        title_matches = re.findall(title_pattern, html)
        print(f"从title找到 {len(title_matches)} 只股票")
        
        for name, code, exchange in title_matches:
            code_pos = html.find(f"{code}.{exchange}")
            if code_pos > 0:
                snippet = html[code_pos:code_pos+500]
                qty_match = re.search(r'title="(\d+)股"', snippet)
                quantity = qty_match.group(1) if qty_match else "0"
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
        
        print(f"提取到 {len(holdings)} 条持仓记录")
        
        result = {
            "holdings": holdings,
            "backtest_id": backtest_id,
        }
        
        # 抓取日志
        if fetch_logs and holdings:
            print("\n正在抓取交易日志...")
            print("  （日志加载较慢，等待最多15秒...）")
            try:
                log_tab_selectors = [
                    "a:has-text('日志')",
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
                
                for i in range(15):
                    page.wait_for_timeout(1000)
                    html = page.content()
                    if "正在加载日志" not in html and "loading_s" not in html:
                        print(f"  日志加载完成（{i+1}秒）")
                        break
                
                log_html = page.content()
                with open("/tmp/jq_log_page_cookie.html", "w", encoding="utf-8") as f:
                    f.write(log_html)
                
                trades = []
                trade_patterns = [
                    r'<tr[^>]*>.*?([\d-]{4,10}).*?(\d{6}).*?([\u4e00-\u9fa5]+).*?(买入|卖出|买|卖).*?(\d+).*?([\d.]+).*?</tr>',
                ]
                
                for pattern in trade_patterns:
                    matches = re.findall(pattern, log_html, re.DOTALL)
                    if matches:
                        for m in matches:
                            trades.append({
                                "date": m[0],
                                "code": m[1],
                                "name": m[2] if m[2] not in ["买入","卖出","买","卖"] else "",
                                "side": m[3] if m[3] in ["买入","卖出","买","卖"] else "买入",
                                "quantity": m[-2] if m[-2].isdigit() else m[-3],
                                "price": m[-1],
                            })
                        break
                
                if not trades:
                    pre_blocks = re.findall(r'<pre[^>]*>([\s\S]*?)</pre>', log_html)
                    for pre in pre_blocks:
                        lines = pre.strip().split('\n')
                        for line in lines:
                            log_match = re.search(r'([\d-]{4,10}).*?(买入|卖出|买|卖)\s+(\d{6}).*?(\d+)\s*股?\s*@?\s*([\d.]+)', line)
                            if log_match:
                                trades.append({
                                    "date": log_match.group(1),
                                    "code": log_match.group(3),
                                    "side": log_match.group(2),
                                    "quantity": log_match.group(4),
                                    "price": log_match.group(5),
                                })
                
                if trades:
                    print(f"提取到 {len(trades)} 条交易日志")
                    result["trades"] = trades
                else:
                    print("日志页面为空或格式不匹配")
                    result["trades"] = []
                    
            except Exception as e:
                print(f"抓取日志失败: {e}")
                result["trades"] = []
        
        browser.close()
        return result


def main():
    backtest_id = os.environ.get("JQ_BACKTEST_ID", "054e0e6887a2e77a64468e68d8419535")
    
    data = fetch_with_cookies(backtest_id, fetch_logs=True)
    if not data:
        print("抓取失败")
        return 1
    
    print("\n=== 持仓数据 ===")
    print(json.dumps(data, ensure_ascii=False, indent=2))
    
    # 保存到数据库
    print("\n正在导入数据库...")
    db_path = os.path.abspath(DB_PATH)
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    imported = 0
    for h in data.get("holdings", []):
        try:
            code = h["code"]
            if "." not in code:
                code = code + ".SH" if code.startswith("6") else code + ".SZ"
            qty = int(h.get("quantity", 0))
            if qty <= 0:
                continue
            cost = float(h.get("cost", 0)) or 100.0
            cursor.execute("""
                INSERT OR REPLACE INTO portfolio_events
                (event_type, event_date, symbol, side, quantity, price, note)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, ("trade", datetime.now().strftime("%Y-%m-%d"), code, "buy", qty, cost, h.get("name", "")))
            imported += 1
        except Exception as e:
            print(f"导入失败: {e}")
    
    conn.commit()
    conn.close()
    print(f"成功导入 {imported} 条持仓")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
