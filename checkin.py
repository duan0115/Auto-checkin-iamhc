import requests
import sys
import os
from datetime import datetime
from urllib.parse import quote

EMAIL         = os.environ.get("EMAIL") or ""
PASSWORD      = os.environ.get("PASSWORD") or ""
TG_CHAT_ID    = os.environ.get("TG_CHAT_ID") or ""
TG_BOT_TOKEN  = os.environ.get("TG_BOT_TOKEN") or ""

BASE_URL      = "https://api.iamhc.cn"
QUOTA_PER_UNIT = 500000 # new-api 默认额度换算比例：500000 quota = 1$
TURNSTILE_TOKEN = ""    # 该站点暂未开启 turuntile,暂时用不上此参数

def login(session: requests.Session):
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
        json={"username": EMAIL, "password": PASSWORD},
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
    if not EMAIL or not PASSWORD:
        print("请先设置 EMAIL 和 PASSWORD 环境变量（或在脚本中填写默认值）")
        sys.exit(1)

    session = requests.Session()

    # 登录
    user = login(session)
    if not user:
        print("\n登录失败，无法继续签到")
        sys.exit(1)

    user_id = user["id"]
    username = user.get("username", str(user_id))

    # 获取签到前余额
    info_before = get_user_info(session, user_id)
    if not info_before:
        print("获取用户信息失败")
        sys.exit(1)
    balance_before = quota_to_dollar(info_before.get("quota", 0))

    # 签到
    checkin_data = checkin(session, user_id)

    # 获取签到后余额
    info_after = get_user_info(session, user_id)
    if not info_after:
        print("获取签到后用户信息失败")
        sys.exit(1)
    balance_after = quota_to_dollar(info_after.get("quota", 0))

    # 判断签到结果
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    success = checkin_data.get("success", False)
    msg = str(checkin_data.get("message", ""))

    if success:
        # 签到成功
        awarded_data = checkin_data.get("data", {})
        awarded_quota = awarded_data.get("quota_awarded", 0)
        awarded_dollar = quota_to_dollar(awarded_quota) if awarded_quota else (balance_after - balance_before)
        print(f"✅ 签到成功 | 获得: {awarded_dollar}$")

        message = (
            f"🎁 iamhc 签到通知\n\n"
            f"✅ 签到成功,本次签到获得{awarded_dollar}$\n"
            f"👤 登录账户: {username}\n"
            f"💰 昨日余额: {balance_before}$\n"
            f"💰 当前余额: {balance_after}$\n"
            f"⏱️ 签到时间: {now}"
        )
    elif "已签到" in msg or "重复签到" in msg or "今天已签到" in msg:
        # 今日已签到
        print(f"✅ 今日已签到 | 当前余额: {balance_after}$")

        message = (
            f"🎁 iamhc 签到通知\n\n"
            f"✅ 今日你已经签到过了！\n"
            f"👤 登录账户: {username}\n"
            f"💰 昨日余额: {balance_before}$\n"
            f"💰 当前余额: {balance_after}$\n"
            f"⏱️ 签到时间: {now}"
        )
    else:
        # 签到失败
        print(f"❌ 签到失败 | {msg}")

        message = (
            f"🎁 iamhc 签到通知\n\n"
            f"❌ 签到失败: {msg}\n"
            f"👤 登录账户: {username}\n"
            f"💰 昨日余额: {balance_before}$\n"
            f"💰 当前余额: {balance_after}$\n"
            f"⏱️ 签到时间: {now}"
        )

    # 发送通知
    send_notification(message)


if __name__ == "__main__":
    main()