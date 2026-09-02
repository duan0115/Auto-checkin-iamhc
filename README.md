# iamhc 自动签到脚本

自动登录 [api.hcnsec.cn](https://api.hcnsec.cn) 并执行每日签到，支持**单账号 / 多账号**模式，签到后通过 Telegram 推送汇总通知(可选)。

### 配置 Secrets

在仓库 **Settings → Secrets and variables → Actions** 中添加以下 Secrets：

| Secret 名称 | 说明 |
|-------------|------|
| `EMAIL` | 登录邮箱(必填，单账号/多账号写法见下方) |
| `PASSWORD` | 登录密码(仅单账号模式需要) |
| `TG_BOT_TOKEN` | Telegram Bot Token(可选) |
| `TG_CHAT_ID` | Telegram Chat ID(可选) |

### 单账号模式（向后兼容）

`EMAIL` 和 `PASSWORD` 各自填一个值即可：

```
EMAIL=a@a.com
PASSWORD=passwordA
```

### 多账号模式

只需要设置 `EMAIL` 一个 Secret，把多组「邮箱,密码」写在一起：

- 邮箱和密码之间用 **英文逗号 `,`** 分隔
- 多个账号之间用 **`&`** 分隔

```
EMAIL=a@a.com,passwordA&b@b.com,passwordB&c@c.com,passwordC
```

此时可以不用设置 `PASSWORD`（即使设置了也会被忽略）。

脚本会依次登录每个账号并执行签到，账号之间自动间隔 1 秒，避免请求过于集中；最后把所有账号的签到结果（成功/已签到/失败数量 + 每个账号的余额变化）汇总成一条 Telegram 通知统一推送，控制台日志打码，Telegram 通知显示完整邮箱。只要有任意一个账号签到失败或出现异常，脚本会以非零状态码退出，方便在 Actions 里观察。

### 手动触发

在仓库 **Actions** 页面选择 `iamhc Daily Checkin` 工作流，点击 **Run workflow** 即可手动触发。

## 获取 Telegram Bot Token 和 Chat ID

1. 在 Telegram 中搜索 `@BotFather`，发送 `/newbot` 创建机器人，获取 **Bot Token**
2. 搜索 `@userinfobot`，发送任意消息，获取你的 **Chat ID**
3. 先给你的 Bot 发一条消息（激活会话），否则 Bot 无法主动推送
