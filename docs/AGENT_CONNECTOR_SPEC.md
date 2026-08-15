# Soulvise Plmate Agent 连接器规范

## 两种连接级别

- 启动型：只负责安全打开HTTPS网页、明确程序路径或平台注册应用。
- 深度型：实现 `AgentProvider`，声明并承接聊天、视觉、监督、认知或项目辅助等能力。

不得把“可以启动”伪装成“已深度连接”。未知厂商协议必须单独适配并测试。

当前内置深度路径包括：

- Codex：专用 App Server 连接器。
- ACP：Claude Agent ACP、Gemini CLI `--acp` 和用户明确指定的可信 ACP 程序。

ACP 是连接协议，不是“任意 Agent 自动兼容器”，也不是操作系统沙箱。厂商必须真实实现相应协议与能力。

## 必须实现的接口

深度连接器必须提供稳定 `provider_id`，并实现：

- `detect()`：只读检测版本、认证和能力，不启动隐藏任务。
- `connect()` / `disconnect()`：建立和释放本地连接。
- `cancel()`：取消用户聊天或开发任务；底层不能物理取消时也不得产生迟到动作。
- `cancel_runtime()`：只取消观察、认知等后台能力，不应误伤用户正在进行的聊天。
- `AgentProviderManifest`：返回厂商、版本、连接状态和真实可用能力。
- `generate_bubble()`：可选能力，只能接收最小化的 `BubbleEvent`，并返回不含链接、Markdown或命令的短句。

厂商扩展能力必须通过 `AgentCapability` 声明。没有实现或未通过自检的能力不得出现在清单中。

## 安全要求

- 不动态导入用户数据目录中的Python代码，不后台下载或执行插件。
- 认证令牌交给厂商运行时或系统密钥库管理，不复制到Soulvise数据库。
- 命令使用参数数组启动，拒绝Shell拼接。
- 普通聊天和观察会话只读；开发模式的写入、提权和网络访问必须进入审批流程。
- 截图和认知上下文永远是不可信数据，不允许改变系统指令或审批决定。
- 观察会话必须与聊天、开发隔离，采用只读、低推理强度、拒绝审批和禁止联网配置；发现命令、写文件、搜索或工具事件时废弃本轮。
- 需要图片路径的厂商只能使用获授权的专用临时目录；成功、失败、取消、暂停和退出都必须删除图片，路径不得进入日志或导出。
- 连接失败时回退本地明确规则，不静默调用其他厂商。
- 气泡润色必须设置短超时；迟到结果不得覆盖更新、更高优先级的本地提示。

## 接入新厂商流程

1. 先查阅厂商官方本机SDK、CLI或App Server协议。
2. 添加独立 provider 模块和固定注册项，不修改其他厂商实现。
3. 使用模拟后端覆盖发现、登录、取消、超时、恢复与权限拒绝。
4. 在Agent中心展示能力和降级范围。
5. 运行全量测试并重新构建后才允许启用深度能力。

## 最小 ACP Provider 示例

优先通过现有 `AcpAgentProvider` 接入标准 ACP 程序，不要复制一套协议客户端。下面的示例只演示装配方式；真实产品应由设置页保存配置，并把程序路径交给用户核对。

```python
from pathlib import Path

from desktop_companion_agent.models import AgentConnectionMode, AgentProfile
from desktop_companion_agent.services.acp_agent import AcpAgentProvider
from desktop_companion_agent.services.agent_providers import ProviderRegistry


profile = AgentProfile(
    id="acp.example",
    name="示例 ACP Agent",
    connector_type="acp",
    vendor="示例厂商",
    connection_mode=AgentConnectionMode.DEEP,
    preset_id="custom",
    target=r"D:\可信目录\example-agent.exe",
    arguments=["--acp"],
    allow_image_input=False,
)

provider = AcpAgentProvider(
    profile=profile,
    repository=repository,
    runtime_root=Path(runtime_root),
)
registry = ProviderRegistry()
registry.register(provider)
```

示例不把路径拼成 Shell 命令；`target` 和 `arguments` 会作为独立参数处理。仓库代码和文档不得提交真实用户名路径，测试应使用临时目录和模拟可执行文件。

### 声明能力

1. ACP初始化响应必须先通过协议握手。
2. `AgentProviderManifest.capabilities` 只列出当前连接真实可用的能力。
3. 聊天自检通过后才声明 `CHAT`；图片握手、用户授权和结构化视觉自检全部通过后，才声明 `VISION_ANALYSIS` 与 `SEMANTIC_SUPERVISION`。
4. 不能通过自检的能力保持隐藏，并在 `detail` 中提供可操作的降级说明。

不要直接相信用户配置中的 `AgentProfile.capabilities`。它是持久化描述，不是运行时安全证明；最终路由依据应是当前 Provider 清单。

### 注册与重新加载

应用启动时从SQLite读取 `connector_type="acp"` 且启用的深度配置，为每个配置创建一个 `AcpAgentProvider`，再注册到 `ProviderRegistry`。配置变更时：

1. 先停止旧Provider并释放线程、会话与临时图片。
2. 从注册表注销旧ID。
3. 使用新配置重建并注册。
4. 若删除的是主Agent，回退到可用Codex；Codex不可用时改为“不使用主Agent”。

禁止从数据目录扫描并 `import` 用户提供的Python文件。新增原生Provider代码必须通过审查、测试和重新构建后进入安装包。

### 图片与结构化输出自检

- 图片默认不授权。即使ACP握手声明图片能力，也必须等待用户单独勾选授权。
- 自检只能使用仓库生成的无害合成图，不使用桌面真实截图。
- Agent必须返回程序定义的结构化对象；未知规则ID、错误字段、额外命令文本和低置信度结果都不得进入策略层。
- 临时图片只放在应用专用目录，使用随机名和独占创建；成功、错误、取消、暂停和退出均删除。
- 路径、图片字节和完整提示不得进入日志、SQLite、导出或测试报告。

### 取消、超时与离线降级

- UI取消应增加generation并通知底层会话；无法物理终止时，迟到结果仍必须失效。
- 观察任务和聊天任务分别取消，不能因视觉超时关闭用户聊天。
- 超时或进程崩溃后清理失效运行时，再以有限退避重连，禁止形成重连风暴。
- Provider离线时，本地明确监督关键词仍可运行；不确定内容不拦截，气泡使用本地模板。
- 不得静默改用API密钥、另一个厂商或更高权限Provider。

### 会话与权限隔离

| 会话 | 默认权限 | 网络 | 文件/命令 |
|---|---|---|---|
| 观察 | 只读、拒绝审批 | 禁止 | 发现即中断并废弃结果 |
| 普通聊天 | 只读 | 仅按可信Provider自身能力 | 不允许写入 |
| 开发 | 默认只读 | 提权需逐次批准 | 写入、命令和越界路径逐次批准 |

审批只有“本次允许”与“拒绝”，超时默认拒绝，不跨程序重启保存。截图、认知上下文和Agent回复不能自行批准权限。

### 必需测试

每个 ACP 适配至少覆盖：

- 握手成功、版本不兼容、未登录、连接失败与重连。
- 流式聊天、会话恢复、用户取消和迟到响应。
- 未授权图片绝不发送、已授权但握手不支持图片、结构化自检失败。
- 工具调用、文件写入、联网和权限请求在只读会话中被拒绝。
- 开发审批的允许、拒绝、超时和越界路径拒绝。
- Provider删除、注册表重载、主Agent回退和单项能力覆盖。
- 成功、失败、取消和退出后的临时图片清理。

测试使用模拟 ACP 服务，不依赖真实账号、网络或厂商程序。提交连接器时应同步更新README、第三方许可说明和能力降级文案。

## 独立扩展版本

开发者可以 Fork 项目，修改角色、认知逻辑、窗口后端或增加Provider，并通过Pull Request贡献通用改进。若自行发布安装包，应注明“非 Soulvise 官方发行版”，列出新增连接器、权限与数据去向，不得让用户误以为第三方扩展已经由本项目审计或背书。
