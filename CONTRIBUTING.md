# 参与贡献

感谢你帮助改进 Soulvise Plmate。这个项目强调“可扩展”和“可验证”：新能力应通过明确接口加入，并保持用户对截图、权限、认知和 Provider 路由的控制。

## 开始之前

1. 阅读 [README.md](README.md)、[SECURITY.md](SECURITY.md) 与 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。
2. 如果修改 Agent 接入，继续阅读 [docs/AGENT_CONNECTOR_SPEC.md](docs/AGENT_CONNECTOR_SPEC.md)。
3. 不要在 Issue、提交、日志或测试夹具中放入真实 API Key、登录令牌、窗口标题、截图或个人数据。
4. 安全问题不要创建公开 Issue，请按 [SECURITY.md](SECURITY.md) 的方式私下报告。

## 本地开发

项目需要 64 位 Python 3.12：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\test.ps1
```

提交前还应执行：

```powershell
.\.venv\Scripts\python.exe -m compileall -q src tests scripts
git diff --check
```

测试必须使用合成图片、模拟 Agent 和无效示例凭据，不得调用真实账号或打开真实不良内容。

## 分支、提交与 Pull Request

- 维护者功能分支使用 `codex/<功能>`；Fork 贡献者可以使用自己清晰的分支名。
- 每个提交只处理一个可说明的目的，提交说明使用中文。
- Pull Request 应写明动机、影响范围、安全与隐私变化、测试结果和界面截图。
- 功能完成、CI 通过且经维护者确认后才合并到 `main`。
- Dependabot 只创建依赖更新 PR，不自动合并。

## 代码与产品约束

- 关键类、函数和复杂分支使用中文注释，Python 代码通过 Ruff。
- 监督与陪看可以共享一帧截图和一次分析，但认知、事件和导出上下文必须隔离。
- 观察任务只保留最新一帧；暂停、戴眼罩或取消后，迟到结果不得触发动作。
- 截图、窗口标题和 Agent 输出都视为不可信输入。
- 子进程使用路径与参数数组启动，禁止 Shell 字符串拼接。
- 不把密钥、认证令牌、原始截图或完整窗口标题写入配置、SQLite、日志、导出或 Git。
- Provider 只能公开已经实现并通过自检的能力；离线时不得静默切换到未授权服务。
- 开发模式中的写文件、命令、联网或越权路径必须逐次审批。

## 新增 Agent Provider

优先使用厂商官方本机协议或 SDK。一个新 Provider 至少要覆盖：

- 发现、连接、断开、取消、超时和崩溃恢复。
- 能力声明与不可用状态。
- 观察、聊天和开发会话隔离。
- 图片授权与结构化输出自检。
- 权限拒绝、离线降级和迟到结果失效。
- 模拟后端单元测试与全量回归。

不要从用户数据目录动态加载 Python 插件，也不要后台下载并执行未知程序。

## 素材与独立发行版

只提交你有权按 MIT 许可发布的素材。不要提交带个人 EXIF、账号信息或第三方受限版权内容的文件。

Fork 可以修改角色、认知逻辑、连接器和平台后端。若公开独立安装包，请明显注明“非 Soulvise 官方发行版”，并说明新增 Provider、网络请求和数据处理方式。
