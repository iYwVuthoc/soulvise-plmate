"""Codex SDK 打包隔离策略测试。"""

from __future__ import annotations

import runpy
from pathlib import Path

from nuitka.utils.ModuleNames import ModuleName


def test_codex_sdk_is_frozen_as_bytecode_only() -> None:
    """Codex 协议模型必须保持字节码，其他模块仍交给 Nuitka 编译。"""

    plugin_path = (
        Path(__file__).parents[1] / "packaging" / "nuitka_codex_bytecode.py"
    )
    namespace = runpy.run_path(str(plugin_path))
    plugin_class = namespace["NuitkaPluginCodexBytecode"]
    plugin = plugin_class()

    assert plugin.decideCompilation(ModuleName("openai_codex")) == "bytecode"
    assert (
        plugin.decideCompilation(ModuleName("openai_codex.generated.v2_all"))
        == "bytecode"
    )
    assert plugin.decideCompilation(ModuleName("desktop_companion_agent.app")) is None
