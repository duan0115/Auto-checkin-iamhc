#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os, sys, time, requests
from urllib.parse import quote

TG_CHAT_ID    = os.environ.get("TG_CHAT_ID") or ""
TG_BOT_TOKEN  = os.environ.get("TG_BOT_TOKEN") or ""

BASE_URL       = "https://api.hcnsec.cn"
QUOTA_PER_UNIT = 500000  # new-api 默认额度换算比例：500000 quota = 1$
TURNSTILE_TOKEN = ""     # 该站点暂未开启 turnstile，暂时用不上此参数

# 多账号分隔符：账号之间用 "&" 分隔，账号与密码之间用 "," 分隔
# 例如：EMAIL=a@a.com,passwordA&b@b.com,passwordB
ACCOUNT_SEP  = "&"
FIELD_SEP    = ","


def parse_accounts():
    """
    支持两种配置方式：
    1) 单个 EMAIL / PASSWORD 环境变量（向后兼容旧的单账号模式）
    2) EMAIL 环境变量内直接写多组 "邮箱,密码"，用 "&" 分隔多个账号，
       此时可以不用设置 PASSWORD（也兼容同时设置的情况，PASSWORD 会被忽略）

    返回: [(email, password), ...]
    """
    raw_email = (os.environ.get("EMAIL") or "").strip()
    raw_password = (os.environ.get("PASSWORD") or "").strip()

    accounts = []

    if FIELD_SEP in raw_email:
        # 多账号模式：EMAIL 里包含 "邮箱,密码"
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
        # 单账号模式（向后兼容）
        if raw_email and raw_password:
            accounts.append((raw_email, raw_password))

    return accounts


def login(session: requests.Session, email, password):
    """登录并返回用户信息（id + username）。"""
    login_url = f"{BASE_URL}/api/user/login?turnstile={quote(TURNSTILE_TOKEN)}"

    headers = {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0",
        "Origin": BASE_URL,
        "Referer": f"{BASE_URL}/login",
    }

    resp = session.post(
        login_url,
        headers=headers,
        json={"username": email, "password": password},
        timeout=20,
    )

    if resp.status_code != 200:
        print("登录请求失败:", resp.status_code)
        return None

    data = resp.json()
    if not data.get("success"):
        print("登录失败:", data.get("message", ""))
        return None

    user_data = data.get("data", {})
    user_id = user_data.get("id")
    username = user_data.get("username", "")
    if not user_id:
        print("登录成功但未获取到用户 ID")
        return None

    print(f"✅ 登录成功 | 账户: {username} | ID: {user_id}")
    return {"id": user_id, "username": username}


def get_user_info(session: requests.Session, user_id):
    """获取用户信息，返回 data 字典（包含 quota 等字段）。"""
    url = f"{BASE_URL}/api/user/self"

    headers = {
        "Accept": "application/json, text/plain, */*",
        "User-Agent": "Mozilla/5.0",
        "Referer": BASE_URL,
        "New-Api-User": str(user_id),
    }

    resp = session.get(url, headers=headers, timeout=20)
    data = resp.json()
    if data.get("success"):
        return data.get("data", {})
    return None


def checkin(session: requests.Session, user_id):
    """执行签到，返回签到响应的完整 JSON。"""
    url = f"{BASE_URL}/api/user/checkin"

    headers = {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0",
        "Origin": BASE_URL,
        "Referer": BASE_URL,
        "New-Api-User": str(user_id),
    }

    resp = session.post(url, headers=headers, json={}, timeout=20)
    return resp.json()


def quota_to_dollar(quota):
    """将内部 quota 值转换为美元金额（整数）。"""
    return round(quota / QUOTA_PER_UNIT)


def mask_email(email):
    """简单打码邮箱，避免通知里完整暴露账号。"""
    if "@" not in email:
        return email
    name, domain = email.split("@", 1)
    if len(name) <= 2:
        masked = name[0] + "*"
    else:
        masked = name[0] + "*" * (len(name) - 2) + name[-1]
    return f"{masked}@{domain}"


def process_account(email, password):
    """
    处理单个账号的签到流程，返回一个 dict：
    {
        "email": str,
        "status": "success" | "already" | "failed" | "error",
        "awarded": int,          # 本次签到获得的美元（仅 success 时有意义）
        "balance_before": int,
        "balance_after": int,
        "message": str,          # 失败/异常时的说明
    }
    """
    result = {
        "email": email,
        "status": "error",
        "awarded": 0,
        "balance_before": 0,
        "balance_after": 0,
        "message": "",
    }

    try:
        session = requests.Session()

        user = login(session, email, password)
        if not user:
            result["message"] = "登录失败"
            return result

        user_id = user["id"]
        username = user.get("username", str(user_id))
        result["username"] = username

        info_before = get_user_info(session, user_id)
        if not info_before:
            result["message"] = "获取签到前用户信息失败"
            return result
        balance_before = quota_to_dollar(info_before.get("quota", 0))
        result["balance_before"] = balance_before

        checkin_data = checkin(session, user_id)

        info_after = get_user_info(session, user_id)
        if not info_after:
            result["message"] = "获取签到后用户信息失败"
            return result
        balance_after = quota_to_dollar(info_after.get("quota", 0))
        result["balance_after"] = balance_after

        success = checkin_data.get("success", False)
        msg = str(checkin_data.get("message", ""))

        if success:
            awarded_data = checkin_data.get("data", {})
            awarded_quota = awarded_data.get("quota_awarded", 0)
            awarded_dollar = quota_to_dollar(awarded_quota) if awarded_quota else (balance_after - balance_before)
            result["status"] = "success"
            result["awarded"] = awarded_dollar
            print(f"✅ [{mask_email(email)}] 签到成功 | 获得: {awarded_dollar}$")
        elif "已签到" in msg or "重复签到" in msg or "今天已签到" in msg:
            result["status"] = "already"
            print(f"✅ [{mask_email(email)}] 今日已签到 | 当前余额: {balance_after}$")
        else:
            result["status"] = "failed"
            result["message"] = msg
            print(f"❌ [{mask_email(email)}] 签到失败 | {msg}")

        return result

    except Exception as e:
        result["message"] = str(e)
        print(f"❌ [{mask_email(email)}] 发生异常: {e}")
        return result


def build_unified_message(results, now):
    """把所有账号的结果汇总成一条统一的通知文本。"""
    total = len(results)
    success_count = sum(1 for r in results if r["status"] == "success")
    already_count = sum(1 for r in results if r["status"] == "already")
    failed_count = sum(1 for r in results if r["status"] in ("failed", "error"))

    lines = []
    lines.append("🎁 iamhc 签到通知（多账号汇总）")
    lines.append("")
    lines.append(f"📊 共 {total} 个账号 | ✅ 成功 {success_count} | 🔁 已签到 {already_count} | ❌ 失败 {failed_count}")
    lines.append(f"⏱️ 签到时间: {now}")
    lines.append("")

    for idx, r in enumerate(results, start=1):
        masked = mask_email(r["email"])
        lines.append(f"—— 账号 {idx}: {masked} ——")
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
            tg_url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
            resp = requests.post(
                tg_url,
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
        print("单账号: EMAIL=a@a.com  PASSWORD=xxxx")
        print("多账号: EMAIL=a@a.com----passwordA&b@b.com----passwordB")
        sys.exit(1)

    print(f"共检测到 {len(accounts)} 个账号，开始依次签到...\n")

    results = []
    for email, password in accounts:
        r = process_account(email, password)
        results.append(r)
        time.sleep(1)  # 账号之间稍作间隔，避免请求过于集中

    local_time = time.gmtime(time.time() + 8 * 3600)
    now = time.strftime("%Y-%m-%d %H:%M:%S", local_time)

    unified_message = build_unified_message(results, now)
    send_notification(unified_message)

    # 只要有账号失败，就以非零状态码退出，方便在 Actions 里观察
    if any(r["status"] in ("failed", "error") for r in results):
        sys.exit(1)


if __name__ == "__main__":
    main()
