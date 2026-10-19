# Steam Freebie

每天抓取 Steam 商店中正在进行的 100% 折扣游戏与 DLC，与上一次结果比较，并把新增内容通过邮件发送。运行状态保存在 GitHub Actions 仓库变量 `STEAM_FREEBIE_STATE` 中。

## 工作流程

1. GitHub Actions 每天 `19:00 UTC`（北京时间次日 `03:00`）运行，也支持手动运行。
2. 抓取 Steam 游戏和 DLC 搜索结果并自动翻页、去重。
3. 读取 `STEAM_FREEBIE_STATE`，找出本次新增内容。
4. 仅在存在新增内容时发送邮件。
5. 邮件发送成功（或没有新增内容）后，将本次标识列表写回仓库变量。

首次运行时没有历史状态，因此当前找到的全部内容都会作为新增发送。

## GitHub 配置

在仓库的 **Settings → Secrets and variables → Actions** 中配置：

### Secrets

| 名称 | 必需 | 说明 |
| --- | --- | --- |
| `REPO_VARIABLE_TOKEN` | 是 | Fine-grained PAT，限定到本仓库并授予 `Variables: Read and write` |
| `SMTP_HOST` | 是 | SMTP 服务器，例如 `smtp.qq.com` |
| `SMTP_USERNAME` | 是 | SMTP 登录用户名，通常为发件邮箱 |
| `SMTP_PASSWORD` | 是 | SMTP 密码或邮箱授权码 |
| `STEAM_COOKIE` | 否 | Steam 完整 Cookie；公开搜索通常不需要 |

### Variables

| 名称 | 必需 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `MAIL_TO` | 是 | - | 收件地址，多个地址用逗号分隔 |
| `MAIL_FROM` | 否 | `SMTP_USERNAME` | 邮件 From 地址 |
| `SMTP_PORT` | 否 | `465` | SMTP 端口 |
| `SMTP_USE_SSL` | 否 | `true` | 是否使用隐式 TLS（常见于 465） |
| `SMTP_STARTTLS` | 否 | `false` | 是否使用 STARTTLS（常见于 587） |
| `STEAM_FREEBIE_STATE` | 否 | - | 脚本首次成功运行后自动创建/更新 |

如果使用 587 端口，请设置 `SMTP_PORT=587`、`SMTP_USE_SSL=false`、`SMTP_STARTTLS=true`。

## 本地运行

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python script.py
```

本地运行默认只生成 `steam_games.json` 和 `steam_state.json`，不会发邮件。设置所需环境变量后添加 `--send-email` 才会发送。

> Steam 商店 HTML 结构可能变化；页面缺少预期的搜索结果容器时，脚本会主动失败且不会覆盖已保存状态。合法的零结果页面仍会保存为空状态。
