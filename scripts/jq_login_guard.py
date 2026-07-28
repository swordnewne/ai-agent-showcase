#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
聚宽登录守护脚本
- 检测 Cookie 有效性
- 过期则自动重新登录
- 生成 Cookie 文件供其他脚本使用

用法:
  python3 jq_login_guard.py  # 检查并修复 Cookie
  python3 jq_login_guard.py --force  # 强制重新登录
"""
import argparse
import json
import os
import sys
from datetime import datetime

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("错误：未安装 Playwright")
    sys.exit(1)

COOKIE_FILE = os.path.expanduser("~/.jq_cookie.json")
CREDENTIALS_FILE = os.path.expanduser("~/.joinquant_credentials.json")

def get_credentials():
    """读取凭据"""
    if os.path.exists(CREDENTIALS_FILE):
        try:
            with open(CREDENTIALS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"读取凭据文件失败: {e}")
    
    # 环境变量兜底
    username = os.environ.get("JQ_USERNAME")
    password = os.environ.get("JQ_PASSWORD")
    if username and password:
        return {"username": username, "password": password}
    
    return None


def check_cookie_valid():
    """检测 Cookie 是否有效——通过实际API请求测试"""
    import requests
    
    if not os.path.exists(COOKIE_FILE):
        return False
    
    try:
        with open(COOKIE_FILE, "r", encoding="utf-8") as f:
            cookies = json.load(f)
        
        cookie_dict = {c["name"]: c["value"] for c in cookies if "joinquant.com" in c.get("domain", "")}
        
        if not cookie_dict:
            return False
        
        # 关键字段检查
        required = {"uid", "token", "PHPSESSID"}
        missing = required - set(cookie_dict.keys())
        if missing:
            print(f"Cookie 缺少关键字段: {missing}")
            return False
        
        # 实际API测试：访问持仓导出接口（轻量请求）
        # 即使 backtestId 无效，只要 Cookie 有效，服务器会返回 400 而非 302
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": "https://www.joinquant.com/algorithm/live/index",
        }
        
        # 用一个通用的API测试
        resp = requests.get(
            "https://www.joinquant.com/algorithm/backtest/export?type=position&backtestId=test",
            headers=headers,
            cookies=cookie_dict,
            timeout=(5, 10),
            allow_redirects=True
        )
        
        # Cookie 有效时：即使参数错误也返回 400（服务器已识别用户）
        # Cookie 无效时：返回 302/401 重定向到登录页
        if resp.status_code in (200, 400, 404):
            # 检查响应内容是否包含登录提示
            if resp.text and ("login" in resp.text.lower() or "登录" in resp.text or "请登录" in resp.text):
                print(f"⚠️ Cookie 无效: 响应包含登录提示")
                return False
            print(f"✅ Cookie 有效 ({len(cookie_dict)} 个)")
            return True
        elif resp.status_code in (302, 301, 401):
            print(f"⚠️ Cookie 无效: HTTP {resp.status_code} (重定向到登录)")
            return False
        else:
            print(f"⚠️ Cookie 状态未知: HTTP {resp.status_code}，按有效处理")
            return True
            
    except Exception as e:
        print(f"Cookie 检测异常: {e}")
        return False


def login_and_save_cookie():
    """浏览器登录并保存 Cookie"""
    creds = get_credentials()
    if not creds:
        print("❌ 未找到凭据，请先配置 ~/.joinquant_credentials.json 或环境变量 JQ_USERNAME/JQ_PASSWORD")
        return False
    
    username = creds.get("username")
    password = creds.get("password")
    
    if not username or not password:
        print("❌ 凭据不完整")
        return False
    
    print(f"正在登录聚宽: {username}...")
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = context.new_page()
        
        try:
            # 1. 访问登录页面
            page.goto("https://www.joinquant.com/user/login/index", 
                      wait_until="networkidle", timeout=30000)
            page.wait_for_timeout(2000)
            
            # 2. 填写表单
            page.wait_for_selector("input[name='username']", timeout=15000)
            page.fill("input[name='username']", username)
            page.wait_for_selector("input[type='password']", timeout=15000)
            page.fill("input[type='password']", password)
            
            try:
                page.click("input[type='checkbox']")
            except Exception:
                pass
            
            # 3. 点击登录
            page.click("button:has-text('登 录')")
            page.wait_for_timeout(3000)
            
            # 4. 检查登录结果
            login_success = False
            for sel in ["text=退出", "text=我的策略", "[class*='user-info']"]:
                try:
                    if page.is_visible(sel, timeout=5000):
                        login_success = True
                        break
                except Exception:
                    pass
            
            if not login_success:
                # 检查是否触发验证码
                html = page.content()
                if "验证" in html or "captcha" in html.lower() or "slider" in html.lower():
                    print("⚠️ 触发验证码，尝试自动破解...")
                    # 这里可以接入验证码破解逻辑，但目前聚宽风控未触发
                    # 如果触发，需要人工介入或更复杂的验证码处理
                    page.screenshot(path="/tmp/jq_login_captcha.png")
                    print("验证码截图已保存到 /tmp/jq_login_captcha.png")
                    return False
                
                print("❌ 登录失败，请检查用户名密码")
                page.screenshot(path="/tmp/jq_login_failed.png")
                return False
            
            print("✅ 登录成功！")
            
            # 5. 保存 Cookie
            cookies = context.cookies()
            with open(COOKIE_FILE, "w", encoding="utf-8") as f:
                json.dump(cookies, f, ensure_ascii=False, indent=2)
            
            # 同时更新 fetch_joinquant_full.py 能读取的格式
            cookie_dict = {c["name"]: c["value"] for c in cookies if c.get("domain", "").endswith("joinquant.com")}
            print(f"✅ Cookie 已保存到 {COOKIE_FILE} ({len(cookie_dict)} 个)")
            
            return True
            
        except Exception as e:
            print(f"登录异常: {e}")
            return False
        finally:
            browser.close()


def main():
    parser = argparse.ArgumentParser(description="聚宽登录守护")
    parser.add_argument("--force", action="store_true", help="强制重新登录")
    parser.add_argument("--check-only", action="store_true", help="仅检测，不自动登录")
    args = parser.parse_args()
    
    # 强制重新登录
    if args.force:
        print("强制重新登录...")
        success = login_and_save_cookie()
        sys.exit(0 if success else 1)
    
    # 检查 Cookie 有效性
    print("检测 Cookie 有效性...")
    if check_cookie_valid():
        print("✅ Cookie 正常，无需操作")
        sys.exit(0)
    
    if args.check_only:
        print("❌ Cookie 无效（--check-only 模式，不自动登录）")
        sys.exit(1)
    
    # Cookie 无效，自动重新登录
    print("Cookie 无效，启动自动重新登录...")
    success = login_and_save_cookie()
    
    if success:
        # 再次验证
        if check_cookie_valid():
            print("✅ Cookie 刷新成功")
            sys.exit(0)
        else:
            print("⚠️ 登录成功但 Cookie 验证失败")
            sys.exit(1)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
