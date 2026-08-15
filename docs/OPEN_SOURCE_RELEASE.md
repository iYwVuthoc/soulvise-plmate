# GitHub 开源发布资料

此文件保存公开仓库的非敏感元数据与发布检查项，避免每次发布凭记忆操作。

## 仓库资料

- 仓库：`runyelfy-art/soulvise-plmate`
- 可见性：Public
- 默认分支：`main`
- About：`Agent 的个性化 Agent：面向Windows 10/11，连接Codex及ACP Agent，提供桌面角色、认知、监督、陪看、情绪反馈和可扩展能力路由。`
- Topics：`ai-agent`、`desktop-companion`、`pyside6`、`agent-client-protocol`、`codex`、`claude`、`gemini`、`windows`、`local-first`、`open-source`

## GitHub 首页必须明确的信息

- 当前正式支持：Windows 10/11 64位。
- 暂未正式支持：macOS、Linux；只保留跨平台架构和安全降级设计，不提供官方安装包。
- 官方原始功能：桌面角色、统一观察、监督拦截、语义陪看、认知积累、高兴值与庆祝奖杯、角色气泡、Codex深度连接、ACP连接、主Agent与单项能力路由、参数及隐私数据管理。
- “ACP兼容”不得描述成“任意Agent自动完整兼容”；真实能力必须经过握手、授权和结构化自检。

## 发布边界

- 首次公开只推送清理后的 v0.2.0 快照，不推送本机旧 v0.1.x 历史。
- 二进制只上传 GitHub Release，不提交到源码分支。
- 本机旧历史保存在未推送的 `codex/archive-pre-open-source` 分支。
- GitHub 登录使用浏览器授权，不在命令、对话或配置文件中粘贴令牌。
- 稳定版本才创建标签和 Release；普通提交与 PR 不附带安装器。

## 每次发布前

1. 全量运行测试、Ruff、编译检查和 Windows 冒烟测试。
2. 扫描工作树与拟公开历史中的密钥、令牌、私钥、数据库、日志、配置和绝对用户路径。
3. 检查图片 EXIF 与其他可识别元数据。
4. 核对版本号、安装器信息、文件大小和 SHA-256。
5. 验证 Release 二进制与本机已验收文件哈希一致。
6. 由维护者确认后推送、合并并发布。

不要配置“保存文件即自动推送”。GitHub Actions 只验证提交；Dependabot 只提出升级 PR，均不自动合并。
