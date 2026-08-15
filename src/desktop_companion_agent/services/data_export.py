"""不含密钥和原始截图的用户数据导出。"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

from desktop_companion_agent.config import ConfigManager
from desktop_companion_agent.models import RuleMode
from desktop_companion_agent.storage.repository import CognitionRepository


class DataExportService:
    """生成便于用户检查和迁移的 ZIP 数据包。"""

    def __init__(
        self,
        config_manager: ConfigManager,
        repository: CognitionRepository,
        context_dir: Path,
    ):
        self.config_manager = config_manager
        self.repository = repository
        self.context_dir = Path(context_dir)

    @staticmethod
    def _json(value: object) -> str:
        return json.dumps(value, ensure_ascii=False, indent=2)

    def export(self, destination: Path) -> Path:
        """导出非敏感配置、认知规则、事件摘要和只读上下文。"""

        path = Path(destination)
        path.parent.mkdir(parents=True, exist_ok=True)
        supervision_rules = [
            item.model_dump(mode="json")
            for item in self.repository.list_rules(RuleMode.SUPERVISION)
        ]
        companion_rules = [
            item.model_dump(mode="json") for item in self.repository.list_rules(RuleMode.COMPANION)
        ]
        events = [item.model_dump(mode="json") for item in self.repository.list_events(limit=1000)]
        pending = [item.model_dump(mode="json") for item in self.repository.list_pending_changes()]
        profiles = [item.model_dump(mode="json") for item in self.repository.list_agent_profiles()]
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                "config.json",
                self._json(self.config_manager.config.model_dump(mode="json")),
            )
            archive.writestr("rules/supervision.json", self._json(supervision_rules))
            archive.writestr("rules/companion.json", self._json(companion_rules))
            archive.writestr("events.json", self._json(events))
            archive.writestr("pending_changes.json", self._json(pending))
            archive.writestr("agent_profiles.json", self._json(profiles))
            archive.writestr(
                "README.txt",
                "此导出不含 API 密钥、聊天正文、完整窗口标题或原始截图。\n",
            )
            for name in ("supervision_context.md", "companion_context.md"):
                context_path = self.context_dir / name
                if context_path.is_file():
                    archive.write(context_path, f"contexts/{name}")
        return path
