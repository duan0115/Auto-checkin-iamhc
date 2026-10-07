import sys, time
from urllib.parse import quote
from playwright.sync_api import sync_playwright

# 复用原 checkin.py 里的账号解析、打码、汇总、通知
from checkin import (
    BASE_URL, parse_accounts, quota_to_dollar, mask_email,
    build_unified_message, send_notification,
)

# 在页面内发 fetch：cookie 留在浏览器里，和真实点击行为一致
JS_FETCH = """
async ([url, method, body, headers]) => {
  const h = Object.assign({'Accept': 'application/json'},
    body ? {'Content-Type': 'application/json'} : {}, headers || {});
  const r = await fetch(url, {
    method, credentials: 'include', headers: h,
    body: body ? JSON.stringify(body) : undefined
  });
  try { return await r.json(); }
  catch (e) { return {success: false, message: 'HTTP ' + r.status}; }
}
"""

TOKEN_JS = """
() => {
  const i = document.querySelector('input[name="cf-turnstile-response"]');
  return i ? i.value : '';
}
"""


def api(page, path, method="GET", body=None, headers=None):
    return page.evaluate(JS_FETCH, [BASE_URL + path, method, body, headers])


def get_turnstile_token(page, timeout=60):
    """打开登录页，等 Turnstile 小组件自动产出 token。"""
    page.goto(f"{BASE_URL}/login", wait_until="domcontentloaded")
    end = time.time() + timeout
    while time.time() < end:
        token = page.evaluate(TOKEN_JS)
        if token:
            return token
        time.sleep(1)
    return None


def process_account(browser, email, password):
    result = {
        "email": email, "status": "error", "awarded": 0,
        "balance_before": 0, "balance_after": 0, "message": "",
    }
    ctx = browser.new_context(locale="zh-CN")
    page = ctx.new_page()
    try:
        token = get_turnstile_token(page)
        if not token:
            result["message"] = "未能获取 Turnstile token（验证没通过或页面结构变了）"
            return result

        data = api(page, f"/api/user/login?turnstile={quote(token)}", "POST",
                   {"username": email, "password": password})
        if not data.get("success"):
            result["message"] = f"登录失败: {data.get('message', '')}"
            print(f"❌ [{mask_email(email)}] {result['message']}")
            return result

        user = (data.get("data") or {}).get("user") or {}
        uid = user.get("id")
        if not uid:
            result["message"] = f"登录成功但没解析到用户 ID: {data}"
            return result

        headers = {"New-Api-User": str(uid)}
        refresh = api(page, "/api/user/auth/refresh", "POST")
        access_token = ((refresh.get("data") or {}).get("access_token")
                        if refresh.get("success") else None)
        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"

        before = api(page, "/api/user/self", headers=headers)
        if not before.get("success"):
            result["message"] = f"获取用户信息失败: {before.get('message', '')}"
            return result
        result["balance_before"] = quota_to_dollar(before["data"].get("quota", 0))

        # 签到。如果这一步也要求 turnstile，这里的返回信息会写明
        ck = api(page, "/api/user/checkin", "POST", {}, headers)

        after = api(page, "/api/user/self", headers=headers)
        if after.get("success"):
            result["balance_after"] = quota_to_dollar(after["data"].get("quota", 0))

        msg = str(ck.get("message", ""))
        if ck.get("success"):
            awarded_quota = (ck.get("data") or {}).get("quota_awarded", 0)
            result["status"] = "success"
            result["awarded"] = (quota_to_dollar(awarded_quota) if awarded_quota
                                 else result["balance_after"] - result["balance_before"])
            print(f"✅ [{mask_email(email)}] 签到成功 | 获得: {result['awarded']}$")
        elif "已签到" in msg or "重复签到" in msg or "今天已签到" in msg:
            result["status"] = "already"
            print(f"✅ [{mask_email(email)}] 今日已签到")
        else:
            result["status"] = "failed"
            result["message"] = msg
            print(f"❌ [{mask_email(email)}] 签到失败 | {msg}")
        return result
    except Exception as e:
        result["message"] = str(e)
        print(f"❌ [{mask_email(email)}] 发生异常: {e}")
        return result
    finally:
        ctx.close()


def main():
    accounts = parse_accounts()
    if not accounts:
        print("请设置 EMAIL / PASSWORD 环境变量。")
        sys.exit(1)

    results = []
    with sync_playwright() as p:
        # 有头模式（在 Actions 里用 xvfb-run 包一层），比 headless 更容易过 Turnstile
        browser = p.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
        for email, password in accounts:
            results.append(process_account(browser, email, password))
            time.sleep(2)
        browser.close()

    now = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(time.time() + 8 * 3600))
    send_notification(build_unified_message(results, now))

    if any(r["status"] in ("failed", "error") for r in results):
        sys.exit(1)


if __name__ == "__main__":
    main()
