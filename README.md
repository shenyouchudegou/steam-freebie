# Steam Freebie

每天抓取 Steam 商店正在进行的 100% 折扣游戏与 DLC，与上一次结果比较，并通过邮件通知订阅者。用户通过 GitHub Discussions 自助订阅或退订，公开区域只提交 PGP 密文，不出现明文邮箱。

## 用户订阅指南

### 1. 导入项目公钥

从仓库根目录下载 `subscribe-pubkey.asc`，然后执行：

```bash
gpg --import subscribe-pubkey.asc
gpg --show-keys --fingerprint subscribe-pubkey.asc
```

请核对 README 中公布的指纹与命令输出一致。维护者部署后应在此处补充公钥指纹：

```text
PGP_FINGERPRINT=待维护者填写
```

### 2. 加密邮箱

把 `<KEY_ID>` 替换为导入时显示的公钥 ID：

```bash
printf 'user@example.com' > email.txt
gpg --armor --encrypt --recipient <KEY_ID> email.txt
```

命令会生成 `email.txt.asc`。不要把原始 `email.txt` 提交到仓库。

### 3. 发起订阅

前往仓库 Discussions 的 `Subscribe` 分类创建讨论：

- 标题严格填写 `Subscribe`。
- “PGP 密文”字段只粘贴从 `-----BEGIN PGP MESSAGE-----` 到 `-----END PGP MESSAGE-----` 的完整内容。
- 不要添加明文邮箱或其他说明。

Action 处理完成后会回复结果并关闭 Discussion。

### 4. 取消订阅

使用同一把项目公钥重新加密要退订的邮箱，然后创建 Discussion：

- 标题严格填写 `Unsubscribe`。
- 正文粘贴完整 PGP 密文。
- 必须使用订阅时的同一个 GitHub 账户。

系统同时匹配 GitHub 数字 ID 和邮箱哈希。失败时统一回复“未找到匹配记录”，不会透露具体不匹配原因。

> PGP 密文会保留在已关闭的公开 Discussion 中。它不会直接暴露邮箱，但若项目私钥将来泄露，历史密文可能被解密。请勿在密文中放入邮箱以外的个人信息。

## 工作原理

1. `.github/workflows/subscription.yml` 监听 `Subscribe` 分类的新 Discussion。
2. Action 校验标题和正文，拒绝明文邮箱，使用 PGP 私钥解密并规范化邮箱。
3. 订阅记录使用 AES-256-GCM 加密后写入仓库变量 `SUBSCRIBERS_ENC`，不会产生数据提交。
4. `.github/workflows/main.yml` 每日抓取 Steam，找出较上次新增的免费内容。
5. 有新增内容时解密订阅列表，通过 Resend Batch API 为每位订阅者生成独立邮件。
6. 通知成功后更新 `STEAM_FREEBIE_STATE`；失败时不推进状态，以便重新运行。

## 维护者部署

### 1. 生成 PGP 密钥

```bash
gpg --full-generate-key
gpg --armor --export <KEY_ID> > subscribe-pubkey.asc
gpg --armor --export-secret-keys <KEY_ID> > private.asc
```

- 提交 `subscribe-pubkey.asc`，绝对不要提交 `private.asc`。
- 将 `private.asc` 的完整文本保存为 Secret `PGP_PRIVATE_KEY`。
- 如果私钥有密码，同时配置 Secret `PGP_PASSPHRASE`。
- 将完整公钥指纹写入上方用户指南。

### 2. 创建列表加密密钥

```bash
openssl rand -base64 32
```

将结果保存为 Secret `LIST_ENCRYPTION_KEY`。此密钥丢失后无法恢复订阅列表，请离线备份。

本项目使用 AES-256-GCM，不兼容旧说明中以 `U2FsdGVkX1...` 开头的 OpenSSL AES-CBC 密文。

### 3. 创建 Fine-grained PAT

创建仅授权本仓库的 Fine-grained PAT：

- Repository access：仅本仓库。
- Repository permissions → Variables：Read and write。

保存为 Secret `VARS_PAT`。Discussion 回复与关闭使用短期 `GITHUB_TOKEN`，PAT 不需要 Discussions 权限。

### 4. 配置 Resend

在 Resend 验证发信域名并创建 API Key，然后配置：

#### Actions Secrets

| 名称 | 必需 | 说明 |
| --- | --- | --- |
| `PGP_PRIVATE_KEY` | 是 | ASCII Armor 格式的 PGP 私钥 |
| `PGP_PASSPHRASE` | 否 | 私钥密码 |
| `LIST_ENCRYPTION_KEY` | 是 | 至少 32 字符的高熵随机密钥 |
| `VARS_PAT` | 是 | 只授予本仓库 Variables 读写权限的 PAT |
| `RESEND_API_KEY` | 是 | Resend API Key |

#### Actions Variables

| 名称 | 必需 | 说明 |
| --- | --- | --- |
| `MAIL_FROM` | 是 | 已验证发件人，例如 `Steam Freebie <notify@example.com>` |
| `SUBSCRIBERS_ENC` | 是 | AES-GCM 加密订阅列表，按下一步初始化 |
| `STEAM_FREEBIE_STATE` | 否 | 免费内容状态，首次成功运行后自动创建 |

### 5. 初始化订阅变量

在本地安装依赖并使用与 Secret 相同的列表密钥：

```bash
python -m pip install -r requirements.txt
export LIST_ENCRYPTION_KEY='<刚才生成的密钥>'
python subscription.py init --output subscribers.enc
gh variable set SUBSCRIBERS_ENC --repo OWNER/REPO < subscribers.enc
```

生成的 `subscribers.enc` 已加入 `.gitignore`。单个 GitHub 变量上限为 48KB，实际容量取决于 PGP 密文长度，通常约为 50–100 条记录。

### 6. 配置 Discussions

启用 GitHub Discussions，创建分类：

- Name：`Subscribe`
- Format：`Open-ended discussion`
- Description：`请阅读仓库 README 中的【订阅指南】。仅接受 PGP 加密邮箱；取消订阅必须使用同一 GitHub 账户。标题填 Subscribe 或 Unsubscribe。`

同时创建 `subscription` 标签。分类 slug 必须是 `subscribe`，以匹配 `.github/DISCUSSION_TEMPLATE/subscribe.yml`。

`subscription.yml` 必须先合并到默认分支，之后创建 Discussion 才会触发处理。

## 本地开发

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python script.py
```

本地运行抓取器默认只生成 `steam_games.json` 和 `steam_state.json`，不会发送邮件。Steam 页面缺少预期结果容器时，脚本会失败且不会覆盖已保存状态。

## 安全边界

- 仓库和日志中不写入明文邮箱，订阅变量仅保存带认证的密文。
- 用户提交的公开 PGP 密文仍依赖项目私钥长期保密。
- 公钥加密只能保护内容，不能证明提交者拥有该邮箱；当前版本没有邮件所有权确认机制，因此仍可能被用于给第三方地址发起订阅。若服务公开推广，建议下一阶段增加双重确认邮件。
