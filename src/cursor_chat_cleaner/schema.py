from __future__ import annotations

import sqlite3
from pathlib import Path


_REQUIRED_TABLES = {
    "ItemTable": frozenset({"key", "value"}),
    "cursorDiskKV": frozenset({"key", "value"}),
    "composerHeaders": frozenset({"composerId", "isArchived", "isSubagent", "value"}),
}

_KV_PREFIXES = (
    "composerData:{id}",
    "bubbleId:{id}:",
    "checkpointId:{id}:",
    "ofsContent:{id}:",
    "codeBlockPartialInlineDiffFates:{id}:",
    "composerVirtualRowHeights:{id}",
)

_ITEM_KEYS = ("glass/cursor/{id}",)


def table_exists(connection: sqlite3.Connection, name: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return row is not None


def table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}


def kv_patterns(composer_id: str) -> list[tuple[str, bool]]:
    patterns: list[tuple[str, bool]] = []
    for template in _KV_PREFIXES:
        key = template.format(id=composer_id)
        if key.endswith(":"):
            escaped = key.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            patterns.append((escaped + "%", True))
        else:
            patterns.append((key, False))
    return patterns


def item_keys(composer_id: str) -> list[str]:
    return [template.format(id=composer_id) for template in _ITEM_KEYS]


def schema_problems(global_db: Path, search_db: Path) -> list[str]:
    if not global_db.is_file():
        return [f"Cursor database not found: {global_db}"]

    problems: list[str] = []
    connection = _connect_readonly(global_db)
    try:
        for table, required in _REQUIRED_TABLES.items():
            if not table_exists(connection, table):
                problems.append(f"missing table {table}")
                continue
            missing = required - table_columns(connection, table)
            if missing:
                problems.append(f"{table} missing columns: {', '.join(sorted(missing))}")

        if table_exists(connection, "ItemTable"):
            row = connection.execute(
                "SELECT value FROM ItemTable "
                "WHERE key = 'composer.composerHeaders.tableGateEnabled'"
            ).fetchone()
            if row is None:
                problems.append("composer.composerHeaders.tableGateEnabled is missing")
            elif _decode(row["value"]).strip().lower() not in {"true", "1"}:
                problems.append("composer.composerHeaders.tableGateEnabled is not true")
    finally:
        connection.close()

    if search_db.is_file():
        search = _connect_readonly(search_db)
        try:
            if not table_exists(search, "conversations"):
                problems.append("conversation-search.db missing table conversations")
            else:
                missing = {"id", "is_archived", "fts_rowid"} - table_columns(
                    search,
                    "conversations",
                )
                if missing:
                    problems.append(
                        "conversations missing columns: " + ", ".join(sorted(missing))
                    )
            if not table_exists(search, "conversation_fts"):
                problems.append("conversation-search.db missing table conversation_fts")
        finally:
            search.close()
    return problems


def assert_writable_schema(global_db: Path, search_db: Path) -> None:
    problems = schema_problems(global_db, search_db)
    if problems:
        raise RuntimeError(
            "Refusing to change Cursor data; schema is not the expected "
            "composerHeaders layout:\n  - " + "\n  - ".join(problems)
        )


def _connect_readonly(path: Path) -> sqlite3.Connection:
    uri = path.resolve().as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _decode(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value or "")
