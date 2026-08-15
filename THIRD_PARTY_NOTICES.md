# 第三方软件声明

Soulvise Plmate 自有源码和仓库内现有美术素材使用 MIT License。项目依赖的第三方软件不因此改为 MIT，而继续遵守各自许可证。

下面列出当前直接依赖与开发/构建工具。版本以 `pyproject.toml`、`requirements.txt` 和 `requirements.lock` 为准；此表是便于核对的摘要，不替代各项目随包附带的完整许可证文本。

| 组件 | 用途 | 许可证 | 项目地址 |
|---|---|---|---|
| PySide6 / Qt for Python | 桌面界面与 Qt 运行库 | LGPL-3.0-only、GPL-2.0-only、GPL-3.0-only 或商业许可（按所选授权方式） | <https://pyside.org/> |
| Pillow | 图片预处理 | MIT-CMU | <https://python-pillow.github.io/> |
| pydantic | 配置与结构化数据校验 | MIT | <https://github.com/pydantic/pydantic> |
| psutil | 进程检测 | BSD-3-Clause | <https://github.com/giampaolo/psutil> |
| python-mss | 屏幕捕获 | MIT | <https://github.com/BoboTiG/python-mss> |
| keyring | 系统密钥库访问 | MIT | <https://github.com/jaraco/keyring> |
| openai-python | 可选 OpenAI API 客户端 | Apache-2.0 | <https://github.com/openai/openai-python> |
| openai-codex | Codex 本机深度连接 | Apache-2.0 | <https://github.com/openai/codex> |
| agent-client-protocol | ACP 本机 Agent 通信 | Apache-2.0 | <https://github.com/agentclientprotocol/python-sdk> |
| pytest | 测试 | MIT | <https://pytest.org/> |
| Ruff | 代码检查 | MIT | <https://docs.astral.sh/ruff/> |
| Nuitka | Windows 构建工具 | AGPL-3.0-or-later；生成产物及随附组件仍按各自条款处理 | <https://nuitka.net/> |
| Inno Setup | Windows 安装器构建工具 | Inno Setup License | <https://jrsoftware.org/isinfo.php> |

当前 `requirements.lock` 中的其他直接或传递依赖按许可证归类如下：

- MIT：annotated-types、anyio、h11、iniconfig、jaraco.classes、jaraco.context、jaraco.functools、jiter、more-itertools、pluggy、pydantic-core。
- BSD-3-Clause：colorama、httpcore、httpx、idna、pywin32-ctypes。
- BSD-2-Clause：Pygments；packaging 可按 Apache-2.0 或 BSD-2-Clause 使用。
- Apache-2.0：distro、openai-codex-cli-bin。
- MPL-2.0：certifi；tqdm 标注为 MPL-2.0 AND MIT。
- MIT OR Apache-2.0：sniffio。
- PSF-2.0：typing_extensions。
- Qt许可组合：PySide6_Addons、PySide6_Essentials、shiboken6 与 PySide6 相同。

构建或再分发安装包时，应保留第三方包携带的许可证文件，并再次核对最终二进制实际包含的 Qt 插件、Codex 运行时及其他组件。若你增加依赖，请在同一个 Pull Request 中更新本文件和锁文件。

本项目名称和界面不表示第三方厂商对 Soulvise Plmate 的背书。Codex、Claude、Gemini 及其他产品名称归各自权利人所有。
