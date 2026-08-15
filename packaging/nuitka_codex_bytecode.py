"""让 Codex Python SDK 在 Nuitka 产物中保持 CPython 字节码语义。

Codex SDK 包含规模很大的 Pydantic 协议模型。将这些模型整体编译为原生 C
代码后，Windows 单文件产物在首次导入 SDK 时可能触发原生栈溢出。这里仅将
``openai_codex`` 包降级为冻结字节码；Soulvise 自身及其余依赖仍由 Nuitka
正常编译，Codex 随包运行时也继续按原方式嵌入。
"""

from nuitka.plugins.PluginBase import NuitkaPluginBase


class NuitkaPluginCodexBytecode(NuitkaPluginBase):
    """指定 Codex SDK 使用冻结字节码，避免打包版首次连接时崩溃。"""

    plugin_name = "soulvise-codex-bytecode"

    def decideCompilation(self, module_name):  # noqa: N802
        """仅调整 ``openai_codex`` 包，不改变其他模块的编译策略。"""

        if module_name.hasNamespace("openai_codex"):
            return "bytecode"
        return None
