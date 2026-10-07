import os, sys, time, requests
from urllib.parse import quote
from playwright.sync_api import sync_playwright

TG_CHAT_ID = os.environ.get("TG_CHAT_ID") or ""
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN") or ""
BASE_URL = "https://api.hcnsec.cn"
QUOTA_PER_UNIT = 500000  # new-api 默认额度换算比例：500000 quota = 1$

# 多账号分隔符：账号之间用 "&" 分隔，账号与密码之间用 "," 分隔
# 例如：EMAIL=a@a.com,passwordA&b@b.com,passwordB
ACCOUNT_SEP = "&"
FIELD_SEP = ","


def parse_accounts():
    """支持单账号（EMAIL + PASSWORD）和多账号（EMAIL=邮箱,密码&邮箱,密码）。"""
    raw_email = (os.environ.get("EMAIL") or "").strip()
    raw_password = (os.environ.get("PASSWORD") or "").strip()
    accounts = []
    if FIELD_SEP in raw_email:
        for item in raw_email.split(ACCOUNT_SEP):
            item = item.strip()
            if not item:
                continue
            parts = item.split(FIELD_SEP)
            if len(parts) != 2:
                print(f"⚠️ 账号配置格式错误，已跳过: {item}")
                continue
            email, password = parts[0].strip(), parts[1].strip()
            if email and password:
                accounts.append((email, password))
    else:
        if raw_email and raw_password:
            accounts.append((raw_email, raw_password))
    return accounts


def quota_to_dollar(quota):
    return round(quota / QUOTA_PER_UNIT)


def mask_email(email):
    if "@" not in email:
        return email
    name, domain = email.split("@", 1)
    if len(name) <= 2:
        masked = name[0] + "*"
    else:
        masked = name[0] + "*" * (len(name) - 2) + name[-1]
    return f"{masked}@{domain}"


# ---------------- 浏览器部分：用真实浏览器过 Turnstile ----------------

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
            print(f"❌ [{mask_email(email)}] {result['message']}")
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
            print(f"❌ [{mask_email(email)}] {result['message']}")
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
            print(f"❌ [{mask_email(email)}] {result['message']}")
            return result
        result["balance_before"] = quota_to_dollar(before["data"].get("quota", 0))

        # 签到。如果这一步也要求 turnstile，返回信息会写明
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
            print(f"✅ [{mask_email(email)}] 今日已签到 | 当前余额: {result['balance_after']}$")
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


# ---------------- 通知 ----------------

def build_unified_message(results, now):
    total = len(results)
    success_count = sum(1 for r in results if r["status"] == "success")
    already_count = sum(1 for r in results if r["status"] == "already")
    failed_count = sum(1 for r in results if r["status"] in ("failed", "error"))

    lines = [
        "🎁 iamhc 签到通知（多账号汇总）",
        "",
        f"📊 共 {total} 个账号 | ✅ 成功 {success_count} | 🔁 已签到 {already_count} | ❌ 失败 {failed_count}",
        f"⏱️ 签到时间: {now}",
        "",
    ]
    for idx, r in enumerate(results, start=1):
        lines.append(f"—— 账号 {idx}: {r['email']} ——")
        if r["status"] == "success":
            lines.append(f"✅ 签到成功，本次获得 {r['awarded']}$")
            lines.append(f"💰 昨日余额: {r['balance_before']}$ → 当前余额: {r['balance_after']}$")
        elif r["status"] == "already":
            lines.append("✅ 今日已经签到过了")
            lines.append(f"💰 当前余额: {r['balance_after']}$")
        elif r["status"] == "failed":
            lines.append(f"❌ 签到失败: {r['message']}")
            lines.append(f"💰 当前余额: {r['balance_after']}$")
        else:
            lines.append(f"❌ 处理异常: {r['message']}")
        lines.append("")
    return "\n".join(lines).strip()


def send_notification(message):
    print("\n" + "=" * 25)
    print(message)
    print("=" * 25)
    if TG_BOT_TOKEN and TG_CHAT_ID:
        try:
            resp = requests.post(
                f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage",
                json={"chat_id": TG_CHAT_ID, "text": message},
                timeout=10,
            )
            if resp.status_code == 200:
                print("Telegram 通知发送成功")
            else:
                print(f"Telegram 通知发送失败: {resp.status_code} {resp.text}")
        except Exception as e:
            print("Telegram 通知发送失败:", e)
    else:
        print("未配置 TG_BOT_TOKEN / TG_CHAT_ID，跳过 Telegram 推送")


def main():
    accounts = parse_accounts()
    if not accounts:
        print("请设置 EMAIL / PASSWORD 环境变量。")
        print("单账号: EMAIL=a@a.com PASSWORD=xxxx")
        print("多账号: EMAIL=a@a.com,passwordA&b@b.com,passwordB")
        sys.exit(1)

    print(f"共检测到 {len(accounts)} 个账号，开始依次签到...\n")
    results = []
    with sync_playwright() as p:
        # 有头模式（Actions 里用 xvfb-run 包一层），比 headless 更容易过 Turnstile
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
