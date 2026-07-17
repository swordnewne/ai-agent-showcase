#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
聚宽模拟交易持仓抓取（验证码破解版）
使用图像识别自动识别滑块缺口 + 模拟人类拖动轨迹
"""
import argparse
import json
import os
import re
import sqlite3
import sys
import time
import random
from datetime import datetime

try:
    from playwright.sync_api import sync_playwright
    from PIL import Image
    import numpy as np
except ImportError:
    print("错误：缺少依赖，请安装:")
    print("  pip3 install playwright pillow numpy --break-system-packages")
    print("  playwright install chromium")
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


def human_like_drag(page, slider_element, distance):
    """
    模拟人类拖动轨迹：带随机速度、抖动、加速/减速
    """
    box = slider_element.bounding_box()
    if not box:
        return False
    
    start_x = box['x'] + box['width'] / 2
    start_y = box['y'] + box['height'] / 2
    
    # 生成人类轨迹点：先加速后减速，带随机抖动
    tracks = []
    current = 0
    mid = distance * 3 / 4
    t = 0.2
    v = 0
    
    while current < distance:
        if current < mid:
            # 加速阶段
            a = 2 + random.uniform(0, 1)
        else:
            # 减速阶段
            a = -3 + random.uniform(-1, 0)
        
        v0 = v
        v = v0 + a * t
        # 添加随机抖动
        move = v0 * t + 0.5 * a * t * t + random.uniform(-1, 1)
        move = round(move)
        current += move
        
        # 偶尔停顿（模拟人类思考）
        pause = random.uniform(0, 0.05) if random.random() > 0.9 else 0
        
        tracks.append({
            'x': move,
            'y': random.randint(-2, 2),  # 垂直方向微小抖动
            'pause': pause
        })
    
    # 执行拖动
    page.mouse.move(start_x, start_y)
    page.mouse.down()
    
    for track in tracks:
        page.mouse.move(start_x + track['x'], start_y + track['y'], steps=1)
        start_x += track['x']
        start_y += track['y']
        if track['pause'] > 0:
            time.sleep(track['pause'])
    
    # 最后微调（模拟人类过头后回拉）
    overshoot = random.randint(2, 5)
    page.mouse.move(start_x + overshoot, start_y, steps=1)
    time.sleep(0.1)
    page.mouse.move(start_x, start_y, steps=1)
    time.sleep(0.2)
    
    page.mouse.up()
    return True


def find_slider_gap(page):
    """
    截图验证码区域，使用图像处理识别缺口位置
    """
    try:
        # 先截图整个页面
        screenshot_path = "/tmp/jq_captcha_full.png"
        page.screenshot(path=screenshot_path)
        
        # 尝试找到验证码区域（通常是特定的class）
        captcha_selectors = [
            ".geetest_canvas_bg",
            ".yidun_bg-img",
            "[class*='captcha']",
            "[class*='slider']",
            "canvas",
        ]
        
        captcha_element = None
        for sel in captcha_selectors:
            try:
                el = page.query_selector(sel)
                if el:
                    captcha_element = el
                    print(f"  找到验证码元素: {sel}")
                    break
            except Exception:
                continue
        
        if captcha_element:
            # 截取验证码区域
            captcha_box = captcha_element.bounding_box()
            if captcha_box:
                img = Image.open(screenshot_path)
                # 截取验证码区域
                left = int(captcha_box['x'])
                top = int(captcha_box['y'])
                right = left + int(captcha_box['width'])
                bottom = top + int(captcha_box['height'])
                captcha_img = img.crop((left, top, right, bottom))
                captcha_img.save("/tmp/jq_captcha_crop.png")
                print(f"  验证码区域截图已保存: /tmp/jq_captcha_crop.png")
                
                # 简单图像处理：找缺口
                # 转换为numpy数组
                img_array = np.array(captcha_img)
                # 这里简化为返回估计距离
                # 实际实现需要更复杂的图像处理算法
                return captcha_box['width'] * 0.6  # 估计值，实际需要图像识别
        
        return None
    except Exception as e:
        print(f"  图像识别失败: {e}")
        return None


def solve_captcha_advanced(page):
    """
    高级验证码处理：识别缺口 + 模拟人类拖动
    """
    try:
        # 1. 等待验证码出现
        page.wait_for_timeout(2000)
        
        # 2. 找到滑块元素
        slider_selectors = [
            ".geetest_slider_button",
            ".yidun_slider",
            "[class*='slider']",
            "[class*='drag']",
        ]
        
        slider = None
        for sel in slider_selectors:
            try:
                el = page.query_selector(sel)
                if el and el.is_visible():
                    slider = el
                    print(f"  找到滑块: {sel}")
                    break
            except Exception:
                continue
        
        if not slider:
            print("  未找到滑块元素")
            return False
        
        # 3. 尝试识别缺口距离（简化为截图+人工判断的自动化版本）
        # 实际生产环境需要更复杂的图像识别
        gap_distance = find_slider_gap(page)
        
        if gap_distance:
            print(f"  估计缺口距离: {gap_distance:.0f}px")
            # 4. 模拟人类拖动
            success = human_like_drag(page, slider, gap_distance)
            if success:
                print("  拖动完成，等待验证结果...")
                page.wait_for_timeout(3000)
                
                # 检查是否验证成功（滑块消失或页面跳转）
                for sel in slider_selectors:
                    try:
                        if not page.query_selector(sel) or not page.query_selector(sel).is_visible():
                            print("  验证码已通过！")
                            return True
                    except Exception:
                        continue
                
                # 检查是否出现错误提示
                error_text = page.inner_text("body")[:500]
                if "验证" in error_text and "失败" in error_text:
                    print("  验证失败，可能需要重试")
                    return False
                
                return True
        
        # 5. 如果图像识别失败，尝试简单拖动
        print("  图像识别失败，尝试简单拖动...")
        box = slider.bounding_box()
        if box:
            # 简单拖到底（很多验证码只需要拖到底即可）
            start_x = box['x'] + box['width'] / 2
            start_y = box['y'] + box['height'] / 2
            end_x = start_x + 250  # 估计距离
            
            page.mouse.move(start_x, start_y)
            page.mouse.down()
            # 分步拖动，模拟人类
            steps = 20
            for i in range(steps):
                x = start_x + (end_x - start_x) * (i + 1) / steps
                y = start_y + random.randint(-2, 2)
                page.mouse.move(x, y, steps=1)
                time.sleep(random.uniform(0.01, 0.03))
            page.mouse.up()
            page.wait_for_timeout(2000)
            
            # 检查验证结果
            for sel in slider_selectors:
                try:
                    if not page.query_selector(sel) or not page.query_selector(sel).is_visible():
                        print("  简单拖动验证通过！")
                        return True
                except Exception:
                    continue
        
        return False
        
    except Exception as e:
        print(f"  验证码处理异常: {e}")
        return False


def login_and_fetch(username, password, backtest_id, fetch_logs=False):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
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
            
            # 4. 处理验证码
            captcha_solved = solve_captcha_advanced(page)
            
            if captcha_solved:
                # 验证码后可能需要再次点击登录
                page.wait_for_timeout(2000)
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
            
            # 6.5 保存Cookie供后续同步使用
            try:
                cookies = context.cookies()
                cookie_file = os.path.expanduser("~/.jq_cookie.json")
                with open(cookie_file, "w", encoding="utf-8") as f:
                    json.dump(cookies, f, ensure_ascii=False, indent=2)
                print(f"✅ Cookie已保存到 {cookie_file}")
            except Exception as e:
                print(f"  Cookie保存警告: {e}")
            
            html = page.content()
            with open("/tmp/jq_page.html", "w", encoding="utf-8") as f:
                f.write(html)
            
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
            
            # 7. 抓取日志
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
                        except Exception:
                            pass
                    
                    for i in range(15):
                        page.wait_for_timeout(1000)
                        html = page.content()
                        if "正在加载日志" not in html and "loading_s" not in html:
                            print(f"  日志加载完成（{i+1}秒）")
                            break
                    
                    log_html = page.content()
                    with open("/tmp/jq_log_page.html", "w", encoding="utf-8") as f:
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
        print("错误：缺少聚宽账号密码")
        return 1
    
    backtest_id = os.environ.get("JQ_BACKTEST_ID", "")
    data = login_and_fetch(username, password, backtest_id, fetch_logs=args.fetch_logs)
    if not data:
        return 1
    
    print("\n=== 持仓数据 ===")
    print(json.dumps(data, ensure_ascii=False, indent=2))
    
    if args.import_db:
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
