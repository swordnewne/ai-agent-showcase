#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
聚宽模拟交易持仓抓取（支持滑块验证码）
"""
import argparse
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

DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "finance.db"
)


def get_credentials():
    username = os.environ.get("JQ_USERNAME")
    password = os.environ.get("JQ_PASSWORD")
    if not username or not password:
        cred_file = os.path.expanduser("~/.joinquant_credentials.json")
        if os.path.exists(cred_file):
            with open(cred_file) as f:
                creds = json.load(f)
                username = creds.get("username")
                password = creds.get("password")
    return username, password


def solve_captcha(page):
    """处理滑块验证码"""
    try:
        # 检查是否有滑块验证码（等待2秒）
        page.wait_for_timeout(2000)
        slider = page.query_selector(".slider, .yidun_slider, [class*='slider']")
        if not slider:
            # 尝试其他选择器
            slider = page.query_selector("[class*='drag']")
        
        if slider:
            print("  检测到滑块验证码，尝试自动破解...")
            # 获取滑块位置
            box = slider.bounding_box()
            if box:
                # 简单拖动到右侧（大多数滑块验证只需拖到底）
                page.mouse.move(box['x'] + box['width']/2, box['y'] + box['height']/2)
                page.mouse.down()
                # 拖动到右侧200像素处（大多数验证码需要拖到底）
                page.mouse.move(box['x'] + 250, box['y'] + box['height']/2, steps=20)
                page.mouse.up()
                print("  已拖动滑块")
                page.wait_for_timeout(2000)
                return True
    except Exception as e:
        print(f"  验证码处理失败: {e}")
    return False


def login_and_fetch(username, password, backtest_id, fetch_logs=False):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1280, "height": 800})
        page = context.new_page()
        
        try:
            # 1. 访问登录页面
            print("正在访问聚宽登录页面...")
            page.goto("https://www.joinquant.com/user/login/index", 
                      wait_until="networkidle", timeout=30000)
            page.wait_for_timeout(2000)
            
            # 2. 填写表单
            print("正在登录...")
            page.wait_for_selector("input[name='username']", timeout=15000)
            page.fill("input[name='username']", username)
            page.wait_for_selector("input[type='password']", timeout=15000)
            page.fill("input[type='password']", password)
            
            try:
                page.click("input[type='checkbox']")
                print("  勾选协议")
            except Exception:
                pass
            
            # 3. 点击登录（可能触发验证码）
            page.click("button:has-text('登 录')")
            page.wait_for_timeout(3000)
            
            # 4. 处理验证码（如果弹出）
            captcha_solved = solve_captcha(page)
            
            if captcha_solved:
                # 验证码后可能需要再次点击登录
                page.wait_for_timeout(2000)
                # 检查是否还在登录页面
                if "login" in page.url:
                    try:
                        page.click("button:has-text('登 录')")
                        page.wait_for_timeout(3000)
                    except Exception:
                        pass
            
            # 5. 检查登录结果
            login_success = False
            for sel in ["text=退出", "text=我的策略", "[class*='user-info']"]:
                try:
                    if page.is_visible(sel, timeout=5000):
                        login_success = True
                        print(f"  登录成功: {sel}")
                        break
                except Exception:
                    pass
            
            if not login_success:
                print("登录失败")
                page.screenshot(path="/tmp/jq_login_failed.png")
                return None
            
            print("登录成功！")
            
            # 6. 获取持仓
            print(f"正在获取持仓数据...")
            page.goto(
                f"https://www.joinquant.com/algorithm/live/index?backtestId={backtest_id}",
                wait_until="networkidle", timeout=30000
            )
            page.wait_for_timeout(3000)
            
            html = page.content()
            with open("/tmp/jq_page.html", "w", encoding="utf-8") as f:
                f.write(html)
            
            # 提取持仓
            holdings = []
            title_pattern = r'title="([^"]+)\((\d{6})\.(XSHG|XSHE)\)"'
            title_matches = re.findall(title_pattern, html)
            
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
            
            # 7. 抓取日志（可选）
            if fetch_logs and holdings:
                print("\n正在抓取交易日志...")
                print("  （日志加载较慢，等待最多15秒...）")
                try:
                    # 点击日志tab
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
                        except Exception:
                            pass
                    
                    # 等待加载完成
                    for i in range(15):
                        page.wait_for_timeout(1000)
                        html = page.content()
                        if "正在加载日志" not in html and "loading_s" not in html:
                            print(f"  日志加载完成（{i+1}秒）")
                            break
                    
                    log_html = page.content()
                    with open("/tmp/jq_log_page.html", "w", encoding="utf-8") as f:
                        f.write(log_html)
                    
                    # 提取交易记录
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
                    
                    # 从<pre>标签提取
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
            
            return result
            
        finally:
            browser.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--backtest-id", help="回测ID")
    parser.add_argument("--import-db", action="store_true")
    parser.add_argument("--fetch-logs", action="store_true")
    args = parser.parse_args()
    
    if args.backtest_id:
        os.environ["JQ_BACKTEST_ID"] = args.backtest_id
    
    username, password = get_credentials()
    if not username:
        return 1
    
    backtest_id = os.environ.get("JQ_BACKTEST_ID", "d8d7a951ece4a7bd995bf9ee62db0273")
    data = login_and_fetch(username, password, backtest_id, fetch_logs=args.fetch_logs)
    if not data:
        return 1
    
    print(json.dumps(data, ensure_ascii=False, indent=2))
    
    if args.import_db:
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
                    INSERT OR REPLACE INTO sig_portfolio_events
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
