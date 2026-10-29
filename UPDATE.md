# Steam-Freebie 订阅系统更新说明

更新日期：2026-10-29

## 本次变更

- 使用 GitHub Discussions 的 `Subscribe` 分类提供自助订阅与退订。
- 公开提交只接受单个 PGP 密文块，检测到明文邮箱或额外正文时拒绝处理。
- 使用不可变 `discussion.user.id` 与规范化邮箱 SHA-256 同时匹配退订记录。
- 使用 AES-256-GCM 加密完整订阅列表并保存到 `SUBSCRIBERS_ENC`，不提交订阅数据文件。
- 使用独立的 Fine-grained PAT 读写仓库变量；Discussion 回复与关闭使用 `GITHUB_TOKEN`。
- 使用 Resend Batch API 发送通知，每个收件人对应一个独立邮件对象，避免地址互相泄露。
- 为批量邮件请求添加幂等键，降低工作流重试造成重复发送的风险。
- 添加订阅并发锁、48KB 容量检查、统一退订失败回复和自动关闭 Discussion。

## 与早期草案的差异

早期草案建议 `openssl enc -aes-256-cbc`。该模式不提供密文完整性认证，本实现改为 AES-256-GCM，变量内容以 `v1.` 开头，不兼容 `U2FsdGVkX1...` 格式。

`VARS_PAT` 只需要本仓库的 `Variables: Read and write` 权限，不需要 Discussions 权限。工作流通过 `permissions: discussions: write` 为临时 `GITHUB_TOKEN` 授权回复和关闭当前 Discussion。

GitHub 单个变量限制为 48KB。因为每条记录还保存一份 ASCII Armor PGP 密文，容量与密钥类型和密文长度有关，应按约 50–100 条记录规划，而不是固定 100 条。

## 部署状态

代码与工作流已就绪，但上线前必须由维护者完成以下外部配置：

1. 生成 PGP 密钥对，提交 `subscribe-pubkey.asc`，将私钥保存到 Actions Secrets。
2. 创建 `LIST_ENCRYPTION_KEY`、`VARS_PAT` 和 `RESEND_API_KEY`。
3. 初始化仓库变量 `SUBSCRIBERS_ENC`。
4. 创建 `Subscribe` Discussion 分类与 `subscription` 标签。
5. 在 Resend 验证发信域名并设置 `MAIL_FROM`。

完整命令与变量说明见 [README.md](README.md)。
