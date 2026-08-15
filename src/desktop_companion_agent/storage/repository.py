"""SQLite 认知、事件和 Agent 配置仓库。"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path

from desktop_companion_agent.models import (
    AgentProfile,
    AgentSession,
    CognitionRule,
    ObservationEvent,
    ObservationEventKind,
    PendingCognitionChange,
    RuleMode,
    RuleSource,
    utc_now_iso,
)
from desktop_companion_agent.storage.starter_rules import (
    STARTER_RULES_METADATA_KEY,
    STARTER_RULES_VERSION,
    starter_supervision_rules,
)


class CognitionRepository:
    """线程安全的 SQLite 仓库。

    仓库永远不接收原始截图，只保存文字摘要、哈希和结构化规则。
    """

    def __init__(self, database_file: Path):
        self.database_file = Path(database_file)
        self.database_file.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(self.database_file, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._initialize()
        self._initialize_starter_supervision_rules()

    def _initialize(self) -> None:
        """创建带约束的数据表和索引。"""

        schema = """
        PRAGMA journal_mode=WAL;
        PRAGMA foreign_keys=ON;
        CREATE TABLE IF NOT EXISTS cognition_rules (
            id TEXT PRIMARY KEY,
            mode TEXT NOT NULL CHECK(mode IN ('supervision', 'companion')),
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            interest_judgment TEXT NOT NULL DEFAULT 'interested',
            keywords_json TEXT NOT NULL,
            examples_json TEXT NOT NULL,
            exclusions_json TEXT NOT NULL,
            priority INTEGER NOT NULL,
            source TEXT NOT NULL,
            enabled INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_rules_mode ON cognition_rules(mode, enabled, priority);

        CREATE TABLE IF NOT EXISTS observation_events (
            id TEXT PRIMARY KEY,
            mode TEXT NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            app_name TEXT NOT NULL,
            window_title_hash TEXT NOT NULL,
            content_id TEXT NOT NULL,
            summary TEXT NOT NULL,
            categories_json TEXT NOT NULL,
            matched_rules_json TEXT NOT NULL,
            interest TEXT NOT NULL,
            happiness_delta INTEGER NOT NULL,
            intervened INTEGER NOT NULL,
            false_positive INTEGER NOT NULL,
            model TEXT NOT NULL,
            confidence REAL NOT NULL,
            event_kind TEXT NOT NULL DEFAULT 'observation',
            pinned INTEGER NOT NULL DEFAULT 0,
            happiness_value INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_events_expiry ON observation_events(expires_at);
        CREATE INDEX IF NOT EXISTS idx_events_mode ON observation_events(mode, created_at DESC);

        CREATE TABLE IF NOT EXISTS pending_changes (
            id TEXT PRIMARY KEY,
            mode TEXT NOT NULL,
            suggestion TEXT NOT NULL,
            source_event_id TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS agent_profiles (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            connector_type TEXT NOT NULL,
            target TEXT NOT NULL,
            arguments_json TEXT NOT NULL,
            model TEXT NOT NULL,
            vendor TEXT NOT NULL DEFAULT 'generic',
            connection_mode TEXT NOT NULL DEFAULT 'launch_only',
            workspace_root TEXT NOT NULL DEFAULT '',
            capabilities_json TEXT NOT NULL DEFAULT '[]',
            app_user_model_id TEXT NOT NULL DEFAULT '',
            preset_id TEXT NOT NULL DEFAULT '',
            allow_image_input INTEGER NOT NULL DEFAULT 0,
            enabled INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS agent_sessions (
            profile_id TEXT NOT NULL,
            purpose TEXT NOT NULL CHECK(purpose IN ('chat', 'runtime', 'development')),
            provider_session_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(profile_id, purpose)
        );

        CREATE TABLE IF NOT EXISTS chat_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('user', 'assistant', 'system')),
            content TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_chat_session ON chat_messages(session_id, id);

        CREATE TABLE IF NOT EXISTS app_metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """
        with self._lock, self._connection:
            self._connection.executescript(schema)
            columns = {
                row[1]
                for row in self._connection.execute("PRAGMA table_info(cognition_rules)").fetchall()
            }
            if "interest_judgment" not in columns:
                self._connection.execute(
                    "ALTER TABLE cognition_rules ADD COLUMN interest_judgment "
                    "TEXT NOT NULL DEFAULT 'interested'"
                )
            agent_columns = {
                row[1]
                for row in self._connection.execute("PRAGMA table_info(agent_profiles)").fetchall()
            }
            agent_additions = {
                "vendor": "TEXT NOT NULL DEFAULT 'generic'",
                "connection_mode": "TEXT NOT NULL DEFAULT 'launch_only'",
                "workspace_root": "TEXT NOT NULL DEFAULT ''",
                "capabilities_json": "TEXT NOT NULL DEFAULT '[]'",
                "app_user_model_id": "TEXT NOT NULL DEFAULT ''",
                "preset_id": "TEXT NOT NULL DEFAULT ''",
                "allow_image_input": "INTEGER NOT NULL DEFAULT 0",
            }
            for name, declaration in agent_additions.items():
                if name not in agent_columns:
                    self._connection.execute(
                        f"ALTER TABLE agent_profiles ADD COLUMN {name} {declaration}"
                    )
            event_columns = {
                row[1]
                for row in self._connection.execute(
                    "PRAGMA table_info(observation_events)"
                ).fetchall()
            }
            event_additions = {
                "event_kind": "TEXT NOT NULL DEFAULT 'observation'",
                "pinned": "INTEGER NOT NULL DEFAULT 0",
                "happiness_value": "INTEGER NOT NULL DEFAULT 0",
            }
            for name, declaration in event_additions.items():
                if name not in event_columns:
                    self._connection.execute(
                        f"ALTER TABLE observation_events ADD COLUMN {name} {declaration}"
                    )

    @staticmethod
    def hash_window_title(title: str) -> str:
        """仅保存窗口标题的不可逆摘要。"""

        return hashlib.sha256(title.encode("utf-8", errors="replace")).hexdigest()

    @staticmethod
    def _rule_values(rule: CognitionRule) -> tuple[object, ...]:
        return (
            rule.id,
            rule.mode.value,
            rule.title,
            rule.description,
            rule.interest_judgment.value,
            json.dumps(rule.keywords, ensure_ascii=False),
            json.dumps(rule.examples, ensure_ascii=False),
            json.dumps(rule.exclusions, ensure_ascii=False),
            rule.priority,
            rule.source.value,
            int(rule.enabled),
            rule.created_at,
            rule.updated_at,
        )

    def _upsert_rule_locked(self, rule: CognitionRule) -> None:
        sql = """
        INSERT OR REPLACE INTO cognition_rules
        (id, mode, title, description, interest_judgment, keywords_json, examples_json,
         exclusions_json, priority, source, enabled, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        self._connection.execute(sql, self._rule_values(rule))

    def add_rule(self, rule: CognitionRule) -> CognitionRule:
        """新增或覆盖一条认知规则。"""

        with self._lock, self._connection:
            self._upsert_rule_locked(rule)
        return rule

    def get_metadata(self, key: str) -> str | None:
        """读取内部一次性迁移标记。"""

        with self._lock:
            row = self._connection.execute(
                "SELECT value FROM app_metadata WHERE key = ?", (key,)
            ).fetchone()
        return None if row is None else str(row["value"])

    def _initialize_starter_supervision_rules(self) -> None:
        """只在首次打开空监督库时注入规则，并始终写入一次性标记。"""

        with self._lock, self._connection:
            marker = self._connection.execute(
                "SELECT value FROM app_metadata WHERE key = ?",
                (STARTER_RULES_METADATA_KEY,),
            ).fetchone()
            if marker is not None:
                return
            count = self._connection.execute(
                "SELECT COUNT(*) FROM cognition_rules WHERE mode = ?",
                (RuleMode.SUPERVISION.value,),
            ).fetchone()[0]
            if int(count) == 0:
                for rule in starter_supervision_rules():
                    self._upsert_rule_locked(rule)
            self._connection.execute(
                "INSERT INTO app_metadata (key, value) VALUES (?, ?)",
                (STARTER_RULES_METADATA_KEY, STARTER_RULES_VERSION),
            )

    def restore_starter_supervision_rules(self) -> list[CognitionRule]:
        """恢复固定内置规则ID，不触碰任何用户或AI规则。"""

        rules = starter_supervision_rules()
        with self._lock, self._connection:
            for rule in rules:
                self._upsert_rule_locked(rule)
            self._connection.execute(
                "INSERT OR REPLACE INTO app_metadata (key, value) VALUES (?, ?)",
                (STARTER_RULES_METADATA_KEY, STARTER_RULES_VERSION),
            )
        return rules

    def list_rules(self, mode: RuleMode, enabled_only: bool = False) -> list[CognitionRule]:
        """按模式读取规则，确保监督与陪看数据隔离。"""

        sql = "SELECT * FROM cognition_rules WHERE mode = ?"
        parameters: list[object] = [mode.value]
        if enabled_only:
            sql += " AND enabled = 1"
        sql += " ORDER BY priority DESC, updated_at DESC"
        with self._lock:
            rows = self._connection.execute(sql, parameters).fetchall()
        return [self._row_to_rule(row) for row in rows]

    def delete_rule(self, rule_id: str) -> None:
        """删除指定规则。"""

        with self._lock, self._connection:
            self._connection.execute("DELETE FROM cognition_rules WHERE id = ?", (rule_id,))

    @staticmethod
    def _row_to_rule(row: sqlite3.Row) -> CognitionRule:
        return CognitionRule(
            id=row["id"],
            mode=RuleMode(row["mode"]),
            title=row["title"],
            description=row["description"],
            interest_judgment=row["interest_judgment"],
            keywords=json.loads(row["keywords_json"]),
            examples=json.loads(row["examples_json"]),
            exclusions=json.loads(row["exclusions_json"]),
            priority=row["priority"],
            source=RuleSource(row["source"]),
            enabled=bool(row["enabled"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def add_event(self, event: ObservationEvent) -> ObservationEvent:
        """保存一条不含截图的观察事件。"""

        values = (
            event.id,
            event.mode.value,
            event.created_at,
            event.expires_at,
            event.app_name[:200],
            event.window_title_hash,
            event.content_id,
            event.summary[:2000],
            json.dumps(event.categories, ensure_ascii=False),
            json.dumps(event.matched_rule_ids, ensure_ascii=False),
            event.interest.value,
            event.happiness_delta,
            int(event.intervened),
            int(event.false_positive),
            event.model[:100],
            event.confidence,
            event.event_kind.value,
            int(event.pinned),
            event.happiness_value,
        )
        sql = """
        INSERT INTO observation_events
        (id, mode, created_at, expires_at, app_name, window_title_hash, content_id,
         summary, categories_json, matched_rules_json, interest, happiness_delta,
         intervened, false_positive, model, confidence, event_kind, pinned,
         happiness_value)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        with self._lock, self._connection:
            self._connection.execute(sql, values)
        return event

    def list_events(self, mode: RuleMode | None = None, limit: int = 200) -> list[ObservationEvent]:
        """读取最近事件。"""

        limit = max(1, min(limit, 1000))
        if mode is None:
            sql = (
                "SELECT * FROM observation_events "
                "ORDER BY pinned DESC, created_at DESC LIMIT ?"
            )
            params = (limit,)
        else:
            sql = (
                "SELECT * FROM observation_events WHERE mode = ? "
                "ORDER BY pinned DESC, created_at DESC LIMIT ?"
            )
            params = (mode.value, limit)
        with self._lock:
            rows = self._connection.execute(sql, params).fetchall()
        return [self._row_to_event(row) for row in rows]

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> ObservationEvent:
        return ObservationEvent(
            id=row["id"],
            mode=RuleMode(row["mode"]),
            created_at=row["created_at"],
            expires_at=row["expires_at"],
            app_name=row["app_name"],
            window_title_hash=row["window_title_hash"],
            content_id=row["content_id"],
            summary=row["summary"],
            categories=json.loads(row["categories_json"]),
            matched_rule_ids=json.loads(row["matched_rules_json"]),
            interest=row["interest"],
            happiness_delta=row["happiness_delta"],
            intervened=bool(row["intervened"]),
            false_positive=bool(row["false_positive"]),
            model=row["model"],
            confidence=row["confidence"],
            event_kind=ObservationEventKind(row["event_kind"]),
            pinned=bool(row["pinned"]),
            happiness_value=row["happiness_value"],
        )

    def delete_event(self, event_id: str) -> None:
        """删除用户明确选择的一条普通或纪念事件。"""

        with self._lock, self._connection:
            self._connection.execute(
                "DELETE FROM pending_changes WHERE source_event_id = ?",
                (event_id,),
            )
            self._connection.execute(
                "DELETE FROM observation_events WHERE id = ?",
                (event_id,),
            )

    def mark_false_positive(self, event_id: str) -> PendingCognitionChange | None:
        """标记误判并生成待审核例外建议。"""

        with self._lock, self._connection:
            row = self._connection.execute(
                "SELECT * FROM observation_events WHERE id = ?", (event_id,)
            ).fetchone()
            if row is None:
                return None
            self._connection.execute(
                "UPDATE observation_events SET false_positive = 1 WHERE id = ?", (event_id,)
            )
            change = PendingCognitionChange(
                mode=RuleMode(row["mode"]),
                suggestion=f"请复核误判事件：{row['summary']}",
                source_event_id=event_id,
            )
            self._connection.execute(
                """
                INSERT INTO pending_changes
                (id, mode, suggestion, source_event_id, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    change.id,
                    change.mode.value,
                    change.suggestion,
                    change.source_event_id,
                    change.status,
                    change.created_at,
                ),
            )
        return change

    def add_pending_change(self, change: PendingCognitionChange) -> None:
        """保存模型提出、等待用户确认的认知建议。"""

        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT OR REPLACE INTO pending_changes
                (id, mode, suggestion, source_event_id, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    change.id,
                    change.mode.value,
                    change.suggestion,
                    change.source_event_id,
                    change.status,
                    change.created_at,
                ),
            )

    def list_pending_changes(self) -> list[PendingCognitionChange]:
        """列出仍待确认的认知建议。"""

        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM pending_changes WHERE status = 'pending' ORDER BY created_at DESC"
            ).fetchall()
        return [
            PendingCognitionChange(
                id=row["id"],
                mode=RuleMode(row["mode"]),
                suggestion=row["suggestion"],
                source_event_id=row["source_event_id"],
                status=row["status"],
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def resolve_pending_change(self, change_id: str, approved: bool) -> None:
        """记录用户对认知建议的处理结果。"""

        status = "approved" if approved else "rejected"
        with self._lock, self._connection:
            self._connection.execute(
                "UPDATE pending_changes SET status = ? WHERE id = ?", (status, change_id)
            )

    def purge_expired_events(self, now: datetime | None = None) -> int:
        """删除过期事件并返回删除数量。"""

        moment = (now or datetime.now(UTC)).isoformat()
        with self._lock, self._connection:
            cursor = self._connection.execute(
                "DELETE FROM observation_events WHERE pinned = 0 AND expires_at < ?",
                (moment,),
            )
        return cursor.rowcount

    def clear_history(self) -> None:
        """清空观察历史和相关待审核建议。"""

        with self._lock, self._connection:
            self._connection.execute("DELETE FROM pending_changes")
            self._connection.execute("DELETE FROM observation_events")

    def clear_cognition(self) -> None:
        """清空全部认知规则、事件和建议。"""

        with self._lock, self._connection:
            self._connection.execute("DELETE FROM pending_changes")
            self._connection.execute("DELETE FROM observation_events")
            self._connection.execute("DELETE FROM cognition_rules")

    def add_agent_profile(self, profile: AgentProfile) -> AgentProfile:
        """新增或更新 Agent 配置。"""

        profile.updated_at = utc_now_iso()
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT OR REPLACE INTO agent_profiles
                (id, name, connector_type, target, arguments_json, model, vendor,
                 connection_mode, workspace_root, capabilities_json, app_user_model_id,
                 preset_id, allow_image_input, enabled, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    profile.id,
                    profile.name,
                    profile.connector_type,
                    profile.target,
                    json.dumps(profile.arguments, ensure_ascii=False),
                    profile.model,
                    profile.vendor,
                    profile.connection_mode.value,
                    profile.workspace_root,
                    json.dumps(
                        [capability.value for capability in profile.capabilities],
                        ensure_ascii=False,
                    ),
                    profile.app_user_model_id,
                    profile.preset_id,
                    int(profile.allow_image_input),
                    int(profile.enabled),
                    profile.created_at,
                    profile.updated_at,
                ),
            )
        return profile

    def list_agent_profiles(self) -> list[AgentProfile]:
        """列出可用 Agent 配置。"""

        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM agent_profiles ORDER BY name COLLATE NOCASE"
            ).fetchall()
        return [
            AgentProfile(
                id=row["id"],
                name=row["name"],
                connector_type=row["connector_type"],
                target=row["target"],
                arguments=json.loads(row["arguments_json"]),
                model=row["model"],
                vendor=row["vendor"],
                connection_mode=row["connection_mode"],
                workspace_root=row["workspace_root"],
                capabilities=json.loads(row["capabilities_json"]),
                app_user_model_id=row["app_user_model_id"],
                preset_id=row["preset_id"],
                allow_image_input=bool(row["allow_image_input"]),
                enabled=bool(row["enabled"]),
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
            for row in rows
        ]

    def delete_agent_profile(self, profile_id: str) -> None:
        """删除 Agent 配置。"""

        with self._lock, self._connection:
            self._connection.execute(
                "DELETE FROM agent_sessions WHERE profile_id = ?", (profile_id,)
            )
            self._connection.execute("DELETE FROM agent_profiles WHERE id = ?", (profile_id,))

    def save_agent_session(self, session: AgentSession) -> AgentSession:
        """保存可恢复的厂商会话ID，不保存认证令牌。"""

        session.updated_at = utc_now_iso()
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT OR REPLACE INTO agent_sessions
                (profile_id, purpose, provider_session_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    session.profile_id,
                    session.purpose,
                    session.provider_session_id,
                    session.created_at,
                    session.updated_at,
                ),
            )
        return session

    def get_agent_session(self, profile_id: str, purpose: str) -> AgentSession | None:
        """读取指定用途的会话指针。"""

        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM agent_sessions WHERE profile_id = ? AND purpose = ?",
                (profile_id, purpose),
            ).fetchone()
        if row is None:
            return None
        return AgentSession(
            profile_id=row["profile_id"],
            purpose=row["purpose"],
            provider_session_id=row["provider_session_id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def delete_agent_session(self, profile_id: str, purpose: str | None = None) -> None:
        """清除失效会话；不影响 Agent 配置。"""

        with self._lock, self._connection:
            if purpose is None:
                self._connection.execute(
                    "DELETE FROM agent_sessions WHERE profile_id = ?", (profile_id,)
                )
            else:
                self._connection.execute(
                    "DELETE FROM agent_sessions WHERE profile_id = ? AND purpose = ?",
                    (profile_id, purpose),
                )

    def add_chat_message(self, session_id: str, role: str, content: str) -> None:
        """保存不含密钥的聊天消息。"""

        if role not in {"user", "assistant", "system"}:
            raise ValueError("无效聊天角色")
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT INTO chat_messages(session_id, role, content, created_at) "
                "VALUES (?, ?, ?, ?)",
                (session_id, role, content[:20000], utc_now_iso()),
            )

    def list_chat_messages(self, session_id: str, limit: int = 100) -> list[dict[str, str]]:
        """按时间顺序返回会话消息。"""

        with self._lock:
            rows = self._connection.execute(
                """
                SELECT role, content, created_at FROM (
                    SELECT id, role, content, created_at FROM chat_messages
                    WHERE session_id = ? ORDER BY id DESC LIMIT ?
                ) ORDER BY id ASC
                """,
                (session_id, max(1, min(limit, 500))),
            ).fetchall()
        return [dict(row) for row in rows]

    def clear_chat_history(self) -> None:
        """清空本地聊天正文与会话元数据，不影响认知规则。"""

        with self._lock, self._connection:
            self._connection.execute("DELETE FROM chat_messages")
            self._connection.execute("DELETE FROM agent_sessions WHERE purpose = 'chat'")

    def close(self) -> None:
        """关闭数据库连接。"""

        with self._lock:
            self._connection.close()
