from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import unquote


def default_user_dir() -> Path:
    return Path.home() / "Library/Application Support/Cursor/User"


def default_projects_dir() -> Path:
    return Path.home() / ".cursor/projects"


@dataclass(frozen=True)
class CursorPaths:
    user_dir: Path = field(default_factory=default_user_dir)
    projects_dir: Path = field(default_factory=default_projects_dir)

    @property
    def global_db(self) -> Path:
        return self.user_dir / "globalStorage" / "state.vscdb"

    @property
    def search_db(self) -> Path:
        return self.user_dir / "globalStorage" / "conversation-search.db"

    @property
    def workspace_storage(self) -> Path:
        return self.user_dir / "workspaceStorage"


@dataclass
class Chat:
    composer_id: str
    title: str
    workspace_id: str
    workspace_path: str
    created_at_ms: int
    updated_at_ms: int
    size_bytes: int
    is_archived: bool = True
    is_subagent: bool = False
    model: str = ""
    lines_added: int = 0
    lines_removed: int = 0
    files_changed: int = 0
    tokens: int = 0
    subcomposer_ids: list[str] = field(default_factory=list)
    transcript_paths: list[Path] = field(default_factory=list)

    @property
    def updated_at(self) -> datetime:
        return datetime.fromtimestamp(self.updated_at_ms / 1000)

    @property
    def repo(self) -> str:
        path = self.workspace_path.rstrip("/")
        if "/" in path:
            return Path(path).name
        return path or self.workspace_id or "-"

    @property
    def ids_to_delete(self) -> list[str]:
        return [self.composer_id, *self.subcomposer_ids]


@dataclass
class DeleteResult:
    chats: int
    composer_ids: list[str]
    kv_rows: int
    header_rows: int
    item_rows: int
    search_rows: int
    transcript_dirs: int
    bytes_removed: int
    vacuumed: bool = False
    backup_path: Path | None = None


@dataclass
class BackupResult:
    path: Path
    chats: int
    kv_rows: int
    header_rows: int
    transcript_dirs: int


_KV_PREFIXES = (
    "composerData:{id}",
    "bubbleId:{id}:",
    "checkpointId:{id}:",
    "ofsContent:{id}:",
    "codeBlockPartialInlineDiffFates:{id}:",
    "composerVirtualRowHeights:{id}",
)


_CURSOR_APP_MARKERS = ("Cursor.app/", "Cursor Nightly.app/")


def _command_is_cursor_app(command: str) -> bool:
    return any(marker in command for marker in _CURSOR_APP_MARKERS)


def cursor_is_running() -> bool:
    try:
        result = subprocess.run(
            ["ps", "-ax", "-o", "command="],
            capture_output=True,
            text=True,
            check=False,
        )
    except (FileNotFoundError, OSError):
        return True
    if result.returncode != 0:
        return True
    return any(_command_is_cursor_app(line) for line in result.stdout.splitlines())


def connect(path: Path, readonly: bool = False) -> sqlite3.Connection:
    if readonly:
        uri = path.resolve().as_uri() + "?mode=ro"
        con = sqlite3.connect(uri, uri=True)
    else:
        con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    return con


def _workspace_path(paths: CursorPaths, workspace_id: str) -> str:
    workspace_json = paths.workspace_storage / workspace_id / "workspace.json"
    if not workspace_json.is_file():
        return workspace_id
    try:
        data = json.loads(workspace_json.read_text())
    except (OSError, json.JSONDecodeError):
        return workspace_id
    folder = data.get("folder") or ""
    if folder.startswith("file://"):
        return unquote(folder[len("file://") :])
    return folder or workspace_id


def _decode(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value or "")


def _json_object(value: object) -> dict:
    raw = _decode(value).strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _transcripts(paths: CursorPaths, composer_id: str) -> list[Path]:
    if not paths.projects_dir.is_dir():
        return []
    return sorted(paths.projects_dir.glob(f"*/agent-transcripts/{composer_id}"))


def _dir_size(path: Path) -> int:
    total = 0
    for child in path.rglob("*"):
        if child.is_file():
            try:
                total += child.stat().st_size
            except OSError:
                continue
    return total


_REQUIRED_TABLES = {
    "ItemTable": frozenset({"key", "value"}),
    "cursorDiskKV": frozenset({"key", "value"}),
    "composerHeaders": frozenset({"composerId", "isArchived", "isSubagent", "value"}),
}


def _has_composer_headers(con: sqlite3.Connection) -> bool:
    row = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='composerHeaders'"
    ).fetchone()
    return row is not None


def _table_columns(con: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in con.execute(f"PRAGMA table_info({table})")}


def schema_problems(paths: CursorPaths) -> list[str]:
    if not paths.global_db.is_file():
        return [f"Cursor database not found: {paths.global_db}"]
    problems: list[str] = []
    con = connect(paths.global_db, readonly=True)
    try:
        for table, required in _REQUIRED_TABLES.items():
            if not _table_exists(con, table):
                problems.append(f"missing table {table}")
                continue
            missing = required - _table_columns(con, table)
            if missing:
                problems.append(f"{table} missing columns: {', '.join(sorted(missing))}")
        if _table_exists(con, "ItemTable"):
            row = con.execute(
                "SELECT value FROM ItemTable WHERE key = 'composer.composerHeaders.tableGateEnabled'"
            ).fetchone()
            if row is not None:
                flag = _decode(row["value"]).strip().lower()
                if flag not in {"true", "1"}:
                    problems.append(
                        "composer.composerHeaders.tableGateEnabled is not true"
                    )
    finally:
        con.close()
    if paths.search_db.is_file():
        search = connect(paths.search_db, readonly=True)
        try:
            if not _table_exists(search, "conversations"):
                problems.append("conversation-search.db missing table conversations")
            else:
                missing = {"id", "is_archived"} - _table_columns(search, "conversations")
                if missing:
                    problems.append(
                        "conversations missing columns: " + ", ".join(sorted(missing))
                    )
        finally:
            search.close()
    return problems


def assert_writable_schema(paths: CursorPaths) -> None:
    problems = schema_problems(paths)
    if problems:
        raise RuntimeError(
            "Refusing to change Cursor data; schema is not the expected "
            "composerHeaders layout:\n  - " + "\n  - ".join(problems)
        )


SORT_KEYS = ("updated", "created", "size", "title", "repo", "workspace")
_DEFAULT_DESCENDING = frozenset({"updated", "created", "size"})


def sort_chats(
    chats: list[Chat],
    key: str = "updated",
    reverse: bool = False,
) -> list[Chat]:
    if key not in SORT_KEYS:
        raise ValueError(f"Unknown sort key {key!r}; choose from {', '.join(SORT_KEYS)}")
    descending = (key in _DEFAULT_DESCENDING) ^ reverse

    def sort_value(chat: Chat):
        if key == "updated":
            return chat.updated_at_ms
        if key == "created":
            return chat.created_at_ms
        if key == "size":
            return chat.size_bytes
        if key == "title":
            return chat.title.casefold()
        if key == "repo":
            return chat.repo.casefold()
        return chat.workspace_path.casefold()

    return sorted(chats, key=lambda chat: (sort_value(chat), chat.composer_id), reverse=descending)


def list_chats(
    paths: CursorPaths,
    *,
    workspace: str | None = None,
    repo: str | None = None,
    older_than_days: int | None = None,
    ids: list[str] | None = None,
    archived_only: bool = True,
    sort: str = "updated",
    reverse: bool = False,
    sizes: bool = True,
) -> list[Chat]:
    if not paths.global_db.is_file():
        raise FileNotFoundError(f"Cursor database not found: {paths.global_db}")

    con = connect(paths.global_db, readonly=True)
    try:
        if not _has_composer_headers(con):
            raise RuntimeError(
                "composerHeaders table missing; this Cursor version is not supported"
            )
        if ids:
            placeholders = ",".join("?" * len(ids))
            rows = con.execute(
                f"""
                SELECT composerId, workspaceId, createdAt, lastUpdatedAt, value,
                       isArchived, isSubagent
                FROM composerHeaders
                WHERE composerId IN ({placeholders})
                ORDER BY lastUpdatedAt DESC
                """,
                ids,
            ).fetchall()
            found = {row["composerId"] for row in rows}
            for missing_id in ids:
                if missing_id in found:
                    continue
                extra = _header_from_composer_data(con, missing_id)
                if extra is not None:
                    rows.append(extra)
        elif archived_only:
            rows = con.execute(
                """
                SELECT composerId, workspaceId, createdAt, lastUpdatedAt, value,
                       isArchived, isSubagent
                FROM composerHeaders
                WHERE isArchived = 1 AND COALESCE(isSubagent, 0) = 0
                ORDER BY lastUpdatedAt DESC
                """
            ).fetchall()
        else:
            rows = con.execute(
                """
                SELECT composerId, workspaceId, createdAt, lastUpdatedAt, value,
                       isArchived, isSubagent
                FROM composerHeaders
                WHERE COALESCE(isSubagent, 0) = 0
                ORDER BY lastUpdatedAt DESC
                """
            ).fetchall()

        chats: list[Chat] = []
        cutoff_ms = None
        if older_than_days is not None:
            cutoff = datetime.now() - timedelta(days=older_than_days)
            cutoff_ms = int(cutoff.timestamp() * 1000)

        for row in rows:
            chat = _chat_from_row(con, paths, row, sizes=sizes)
            if cutoff_ms is not None and chat.updated_at_ms >= cutoff_ms:
                continue
            if workspace:
                needle = workspace.lower()
                if (
                    needle not in chat.workspace_id.lower()
                    and needle not in chat.workspace_path.lower()
                    and needle not in chat.repo.lower()
                ):
                    continue
            if repo and chat.repo.casefold() != repo.casefold():
                continue
            chats.append(chat)
        return sort_chats(chats, key=sort, reverse=reverse)
    finally:
        con.close()


def _header_from_composer_data(con: sqlite3.Connection, composer_id: str) -> dict | None:
    row = con.execute(
        "SELECT value FROM cursorDiskKV WHERE key = ?",
        (f"composerData:{composer_id}",),
    ).fetchone()
    if not row:
        return None
    body = _json_object(row["value"])
    workspace = body.get("workspaceIdentifier") or {}
    workspace_id = workspace.get("id", "") if isinstance(workspace, dict) else ""
    return {
        "composerId": composer_id,
        "workspaceId": workspace_id,
        "createdAt": int(body.get("createdAt") or 0),
        "lastUpdatedAt": int(body.get("lastUpdatedAt") or 0),
        "value": json.dumps({"name": body.get("name") or composer_id}),
        "isArchived": 1 if body.get("isArchived") else 0,
        "isSubagent": 1 if body.get("isBestOfNSubcomposer") else 0,
    }


def _chat_from_row(
    con: sqlite3.Connection, paths: CursorPaths, row, *, sizes: bool = True
) -> Chat:
    composer_id = row["composerId"]
    header = _json_object(row["value"])
    workspace_id = row["workspaceId"] or (header.get("workspaceIdentifier") or {}).get("id", "")
    workspace_path = _workspace_path(paths, workspace_id)
    composer = con.execute(
        "SELECT value FROM cursorDiskKV WHERE key = ?",
        (f"composerData:{composer_id}",),
    ).fetchone()
    body = _json_object(composer["value"] if composer else {})
    subs = [
        sid
        for sid in (body.get("subComposerIds") or body.get("subagentComposerIds") or [])
        if isinstance(sid, str)
    ]
    size_bytes = 0
    transcripts: list[Path] = []
    if sizes:
        size_bytes = _kv_size(con, [composer_id, *subs])
        transcripts = _transcripts(paths, composer_id)
        for sub_id in subs:
            transcripts.extend(_transcripts(paths, sub_id))
        size_bytes += sum(_dir_size(path) for path in transcripts)
    model = ""
    model_config = body.get("modelConfig")
    if isinstance(model_config, dict):
        model = str(model_config.get("modelName") or "").strip()
    return Chat(
        composer_id=composer_id,
        title=(header.get("name") or body.get("name") or composer_id),
        workspace_id=workspace_id,
        workspace_path=workspace_path,
        created_at_ms=int(row["createdAt"] or 0),
        updated_at_ms=int(row["lastUpdatedAt"] or 0),
        size_bytes=size_bytes,
        is_archived=bool(row["isArchived"]),
        is_subagent=bool(row["isSubagent"]),
        model=model,
        lines_added=int(body.get("totalLinesAdded") or header.get("totalLinesAdded") or 0),
        lines_removed=int(body.get("totalLinesRemoved") or header.get("totalLinesRemoved") or 0),
        files_changed=int(body.get("filesChangedCount") or header.get("filesChangedCount") or 0),
        tokens=int(body.get("contextTokensUsed") or 0),
        subcomposer_ids=subs,
        transcript_paths=transcripts,
    )


def _kv_size(con: sqlite3.Connection, composer_ids: list[str]) -> int:
    total = 0
    for composer_id in composer_ids:
        for pattern in _kv_patterns(composer_id):
            if pattern.endswith("%"):
                row = con.execute(
                    "SELECT COALESCE(SUM(length(value)), 0) FROM cursorDiskKV WHERE key LIKE ?",
                    (pattern,),
                ).fetchone()
            else:
                row = con.execute(
                    "SELECT COALESCE(SUM(length(value)), 0) FROM cursorDiskKV WHERE key = ?",
                    (pattern,),
                ).fetchone()
            total += int(row[0])
    return total


def _kv_patterns(composer_id: str) -> list[str]:
    patterns = []
    for template in _KV_PREFIXES:
        key = template.format(id=composer_id)
        patterns.append(key if not key.endswith(":") else key + "%")
    return patterns


def _delete_kv(con: sqlite3.Connection, composer_id: str) -> int:
    deleted = 0
    for pattern in _kv_patterns(composer_id):
        if pattern.endswith("%"):
            cur = con.execute("DELETE FROM cursorDiskKV WHERE key LIKE ?", (pattern,))
        else:
            cur = con.execute("DELETE FROM cursorDiskKV WHERE key = ?", (pattern,))
        deleted += cur.rowcount
    return deleted


def _update_header_cache(con: sqlite3.Connection, removed_ids: set[str]) -> None:
    row = con.execute(
        "SELECT value FROM ItemTable WHERE key = 'composer.composerHeaders'"
    ).fetchone()
    if not row:
        return
    data = _json_object(row["value"])
    composers = data.get("allComposers")
    if not isinstance(composers, list):
        return
    filtered = [
        item
        for item in composers
        if not (isinstance(item, dict) and item.get("composerId") in removed_ids)
    ]
    if len(filtered) == len(composers):
        return
    data["allComposers"] = filtered
    con.execute(
        "UPDATE ItemTable SET value = ? WHERE key = 'composer.composerHeaders'",
        (json.dumps(data, separators=(",", ":")),),
    )


def _delete_search_rows(paths: CursorPaths, composer_ids: list[str]) -> int:
    if not composer_ids or not paths.search_db.is_file():
        return 0
    con = connect(paths.search_db)
    try:
        deleted = 0
        for composer_id in composer_ids:
            rows = con.execute(
                "SELECT fts_rowid FROM conversations WHERE id = ?", (composer_id,)
            ).fetchall()
            for row in rows:
                con.execute("DELETE FROM conversation_fts WHERE rowid = ?", (row[0],))
            cur = con.execute("DELETE FROM conversations WHERE id = ?", (composer_id,))
            deleted += cur.rowcount
            if _table_exists(con, "conversation_search_candidates"):
                con.execute(
                    "DELETE FROM conversation_search_candidates WHERE id = ?",
                    (composer_id,),
                )
        con.commit()
        return deleted
    finally:
        con.close()


def _table_exists(con: sqlite3.Connection, name: str) -> bool:
    row = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def default_backup_dir() -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return Path.home() / "cursor-cleaner-backups" / stamp


def resolve_backup_dir(dest: Path | None) -> Path:
    path = dest.expanduser() if dest is not None else default_backup_dir()
    if path.exists() and any(path.iterdir()):
        path = path / datetime.now().strftime("%Y%m%d-%H%M%S")
    path.mkdir(parents=True, exist_ok=True)
    return path


def backup_chats(paths: CursorPaths, chats: list[Chat], dest: Path | None = None) -> BackupResult:
    if not chats:
        raise ValueError("No chats to back up")
    dest_dir = resolve_backup_dir(dest)
    unique_ids = list(dict.fromkeys(cid for chat in chats for cid in chat.ids_to_delete))

    src = connect(paths.global_db, readonly=True)
    dst_path = dest_dir / "chats.sqlite"
    dst = sqlite3.connect(dst_path)
    try:
        for name in ("ItemTable", "cursorDiskKV", "composerHeaders"):
            row = src.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                (name,),
            ).fetchone()
            if row and row[0]:
                dst.execute(row[0])
        header_cols = [
            info[1] for info in src.execute("PRAGMA table_info(composerHeaders)").fetchall()
        ]
        header_sql = (
            f"INSERT OR REPLACE INTO composerHeaders({', '.join(header_cols)}) "
            f"VALUES ({', '.join('?' * len(header_cols))})"
            if header_cols
            else None
        )
        header_rows = 0
        kv_rows = 0
        for composer_id in unique_ids:
            if header_sql:
                copied = src.execute(
                    f"SELECT {', '.join(header_cols)} FROM composerHeaders WHERE composerId = ?",
                    (composer_id,),
                ).fetchall()
                dst.executemany(header_sql, copied)
                header_rows += len(copied)
            for pattern in _kv_patterns(composer_id):
                if pattern.endswith("%"):
                    rows = src.execute(
                        "SELECT key, value FROM cursorDiskKV WHERE key LIKE ?", (pattern,)
                    ).fetchall()
                else:
                    rows = src.execute(
                        "SELECT key, value FROM cursorDiskKV WHERE key = ?", (pattern,)
                    ).fetchall()
                dst.executemany("INSERT OR REPLACE INTO cursorDiskKV(key, value) VALUES (?, ?)", rows)
                kv_rows += len(rows)
            items = src.execute(
                "SELECT key, value FROM ItemTable WHERE key LIKE ?", (f"%{composer_id}%",)
            ).fetchall()
            dst.executemany("INSERT OR REPLACE INTO ItemTable(key, value) VALUES (?, ?)", items)
        dst.commit()
    finally:
        dst.close()
        src.close()

    search_rows = []
    if paths.search_db.is_file():
        search = connect(paths.search_db, readonly=True)
        try:
            for composer_id in unique_ids:
                for row in search.execute(
                    "SELECT source, scope, id, title, branches, updated_at, is_archived "
                    "FROM conversations WHERE id = ?",
                    (composer_id,),
                ):
                    search_rows.append(dict(row))
        finally:
            search.close()
        if search_rows:
            (dest_dir / "search.json").write_text(json.dumps(search_rows, indent=2))

    transcript_dirs = 0
    transcripts_root = dest_dir / "transcripts"
    seen: set[Path] = set()
    for chat in chats:
        for path in chat.transcript_paths:
            resolved = path.resolve()
            if resolved in seen or not path.is_dir():
                continue
            shutil.copytree(path, transcripts_root / path.name)
            seen.add(resolved)
            transcript_dirs += 1

    manifest = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "chats": [
            {
                "id": chat.composer_id,
                "title": chat.title,
                "workspace": chat.workspace_path,
                "updated_at": chat.updated_at.isoformat(timespec="seconds"),
                "is_archived": chat.is_archived,
                "size_bytes": chat.size_bytes,
                "subcomposer_ids": chat.subcomposer_ids,
            }
            for chat in chats
        ],
    }
    (dest_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return BackupResult(
        path=dest_dir,
        chats=len(chats),
        kv_rows=kv_rows,
        header_rows=header_rows,
        transcript_dirs=transcript_dirs,
    )


def delete_chats(
    paths: CursorPaths,
    chats: list[Chat],
    *,
    vacuum: bool = False,
    backup_dir: Path | None = None,
) -> DeleteResult:
    assert_writable_schema(paths)
    backup_path = None
    if backup_dir is not None:
        backup_path = backup_chats(paths, chats, backup_dir).path

    composer_ids = [cid for chat in chats for cid in chat.ids_to_delete]
    unique_ids = list(dict.fromkeys(composer_ids))
    bytes_removed = sum(chat.size_bytes for chat in chats)

    con = connect(paths.global_db)
    try:
        con.execute("BEGIN IMMEDIATE")
        kv_rows = 0
        header_rows = 0
        item_rows = 0
        for composer_id in unique_ids:
            kv_rows += _delete_kv(con, composer_id)
            cur = con.execute(
                "DELETE FROM composerHeaders WHERE composerId = ?", (composer_id,)
            )
            header_rows += cur.rowcount
            cur = con.execute("DELETE FROM ItemTable WHERE key LIKE ?", (f"%{composer_id}%",))
            item_rows += cur.rowcount
        _update_header_cache(con, set(unique_ids))
        con.commit()
        if vacuum:
            con.execute("VACUUM")
    finally:
        con.close()

    search_rows = _delete_search_rows(paths, unique_ids)

    transcript_dirs = 0
    seen: set[Path] = set()
    for chat in chats:
        for path in chat.transcript_paths:
            resolved = path.resolve()
            if resolved in seen or not path.is_dir():
                continue
            shutil.rmtree(path)
            seen.add(resolved)
            transcript_dirs += 1

    return DeleteResult(
        chats=len(chats),
        composer_ids=unique_ids,
        kv_rows=kv_rows,
        header_rows=header_rows,
        item_rows=item_rows,
        search_rows=search_rows,
        transcript_dirs=transcript_dirs,
        bytes_removed=bytes_removed,
        vacuumed=vacuum,
        backup_path=backup_path,
    )


def format_bytes(size: int) -> str:
    value = float(size)
    for unit in ("B", "K", "M", "G"):
        if value < 1024 or unit == "G":
            if unit == "B":
                return f"{int(value)}{unit}"
            return f"{value:.1f}{unit}"
        value /= 1024
    return f"{size}B"


def format_count(n: int) -> str:
    value = float(n)
    for unit in ("", "K", "M", "B"):
        if value < 1000 or unit == "B":
            if unit == "":
                return str(n)
            return f"{value:.1f}{unit}"
        value /= 1000
    return str(n)


@dataclass
class ChatStats:
    chats: int
    archived: int
    lines_added: int
    lines_removed: int
    files_changed: int
    tokens: int
    models: dict[str, dict[str, int]]
    repos: dict[str, dict[str, int]]


def summarize_repos(chats: list[Chat]) -> list[dict[str, int | str]]:
    repos: dict[str, dict[str, int | str]] = {}
    for chat in chats:
        row = repos.setdefault(chat.repo, {"repo": chat.repo, "chats": 0, "archived": 0})
        row["chats"] = int(row["chats"]) + 1
        row["archived"] = int(row["archived"]) + int(chat.is_archived)
    return sorted(repos.values(), key=lambda row: (-int(row["chats"]), str(row["repo"]).casefold()))


def aggregate_stats(chats: list[Chat]) -> ChatStats:
    models: dict[str, dict[str, int]] = {}
    repos: dict[str, dict[str, int]] = {}
    for chat in chats:
        model = models.setdefault(chat.model or "unknown", {"chats": 0, "tokens": 0})
        model["chats"] += 1
        model["tokens"] += chat.tokens
        repo = repos.setdefault(
            chat.repo,
            {
                "chats": 0,
                "archived": 0,
                "lines_added": 0,
                "lines_removed": 0,
                "files_changed": 0,
                "tokens": 0,
                "models": {},
            },
        )
        repo["chats"] += 1
        repo["archived"] += int(chat.is_archived)
        repo["lines_added"] += chat.lines_added
        repo["lines_removed"] += chat.lines_removed
        repo["files_changed"] += chat.files_changed
        repo["tokens"] += chat.tokens
        model_name = chat.model or "unknown"
        used = repo["models"].setdefault(model_name, {"chats": 0, "tokens": 0})
        used["chats"] += 1
        used["tokens"] += chat.tokens
    for repo in repos.values():
        repo["models"] = dict(
            sorted(repo["models"].items(), key=lambda item: item[1]["tokens"], reverse=True)
        )
    return ChatStats(
        chats=len(chats),
        archived=sum(1 for chat in chats if chat.is_archived),
        lines_added=sum(chat.lines_added for chat in chats),
        lines_removed=sum(chat.lines_removed for chat in chats),
        files_changed=sum(chat.files_changed for chat in chats),
        tokens=sum(chat.tokens for chat in chats),
        models=dict(sorted(models.items(), key=lambda item: item[1]["tokens"], reverse=True)),
        repos=dict(
            sorted(repos.items(), key=lambda item: item[1]["lines_added"], reverse=True)
        ),
    )


def format_stats(stats: ChatStats) -> str:
    lines = [
        f"{stats.chats} chat(s) ({stats.archived} archived)",
        "",
        f"{'MODEL':<22} {'CHATS':>5} {'TOKENS':>8}",
    ]
    if stats.models:
        for name, row in stats.models.items():
            label = name if len(name) <= 22 else name[:21] + "…"
            lines.append(f"{label:<22} {row['chats']:>5} {format_count(row['tokens']):>8}")
    else:
        lines.append("(none)")
    lines += [
        "",
        "ACTIVITY",
        f"  lines   +{stats.lines_added}  -{stats.lines_removed}",
        f"  files   {stats.files_changed}",
        f"  tokens  {format_count(stats.tokens)} context",
        "",
        f"{'REPO':<20} {'CHATS':>5} {'ARCH':>5} {'+LINES':>8} {'-LINES':>8} {'FILES':>6} {'TOKENS':>8}",
    ]
    for repo, row in stats.repos.items():
        label = repo if len(repo) <= 20 else repo[:19] + "…"
        lines.append(
            f"{label:<20} {row['chats']:>5} {row['archived']:>5} "
            f"{row['lines_added']:>8} {row['lines_removed']:>8} "
            f"{row['files_changed']:>6} {format_count(row['tokens']):>8}"
        )
    lines += [
        "",
        f"{'REPO':<20} {'MODEL':<22} {'CHATS':>5} {'TOKENS':>8}",
    ]
    for repo, row in stats.repos.items():
        repo_label = repo if len(repo) <= 20 else repo[:19] + "…"
        models = row.get("models") or {}
        if not models:
            lines.append(f"{repo_label:<20} {'—':<22} {0:>5} {format_count(0):>8}")
            continue
        for name, used in models.items():
            model_label = name if len(name) <= 22 else name[:21] + "…"
            lines.append(
                f"{repo_label:<20} {model_label:<22} {used['chats']:>5} "
                f"{format_count(used['tokens']):>8}"
            )
            repo_label = ""
    return "\n".join(lines) + "\n"


@dataclass
class ChatMessage:
    role: str
    text: str
    created_at: str = ""
    tool_name: str | None = None


def load_conversation(paths: CursorPaths, composer_id: str) -> tuple[Chat, list[ChatMessage]]:
    chats = list_chats(paths, ids=[composer_id], archived_only=False)
    if not chats:
        raise FileNotFoundError(f"Chat not found: {composer_id}")
    chat = chats[0]
    messages = _messages_from_bubbles(paths, chat) or _messages_from_transcripts(chat)
    return chat, messages


def format_conversation(
    chat: Chat,
    messages: list[ChatMessage],
    *,
    thinking: bool = False,
) -> str:
    lines = [
        f"{chat.title}  ({chat.repo})",
        f"{chat.composer_id}  {'archived' if chat.is_archived else 'active'}  "
        f"{chat.updated_at.strftime('%Y-%m-%d %H:%M')}",
        "",
    ]
    for message in messages:
        if message.role == "thinking" and not thinking:
            continue
        if message.role == "tool":
            label = f"TOOL  {message.tool_name or 'tool'}"
        else:
            label = message.role.upper()
        lines.append(label)
        if message.text:
            lines.append(message.text.rstrip())
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _messages_from_bubbles(paths: CursorPaths, chat: Chat) -> list[ChatMessage]:
    con = connect(paths.global_db, readonly=True)
    try:
        raw = con.execute(
            "SELECT value FROM cursorDiskKV WHERE key = ?",
            (f"composerData:{chat.composer_id}",),
        ).fetchone()
        body = _json_object(raw["value"] if raw else {})
        order = [
            header.get("bubbleId")
            for header in (body.get("fullConversationHeadersOnly") or [])
            if isinstance(header, dict) and header.get("bubbleId")
        ]
        rows = con.execute(
            "SELECT key, value FROM cursorDiskKV WHERE key LIKE ?",
            (f"bubbleId:{chat.composer_id}:%",),
        ).fetchall()
    finally:
        con.close()

    bubbles: dict[str, dict] = {}
    for row in rows:
        data = _json_object(row["value"])
        if not data:
            continue
        bubble_id = data.get("bubbleId") or str(row["key"]).rsplit(":", 1)[-1]
        bubbles[bubble_id] = data

    if not bubbles:
        return []

    ids = [bubble_id for bubble_id in order if bubble_id in bubbles]
    ids.extend(bid for bid in bubbles if bid not in ids)
    if not order:
        ids.sort(key=lambda bid: str(bubbles[bid].get("createdAt") or ""))

    messages = []
    for bubble_id in ids:
        message = _message_from_bubble(bubbles[bubble_id])
        if message is not None:
            messages.append(message)
    return messages


def _message_from_bubble(data: dict) -> ChatMessage | None:
    created = str(data.get("createdAt") or "")
    tool = data.get("toolFormerData")
    text = str(data.get("text") or "").strip()
    if isinstance(tool, dict) and tool.get("name"):
        return ChatMessage(
            role="tool",
            text=_tool_detail(tool),
            created_at=created,
            tool_name=str(tool.get("name")),
        )
    thinking = data.get("thinking") or {}
    thinking_text = ""
    if isinstance(thinking, dict):
        thinking_text = str(thinking.get("text") or "").strip()
    if data.get("type") == 1:
        return ChatMessage(role="user", text=_visible_text(text) or text, created_at=created)
    if thinking_text and not text:
        return ChatMessage(role="thinking", text=thinking_text, created_at=created)
    if text:
        return ChatMessage(role="assistant", text=text, created_at=created)
    return None


def _tool_detail(tool: dict) -> str:
    params = tool.get("params")
    if isinstance(params, str):
        try:
            params = json.loads(params)
        except json.JSONDecodeError:
            params = {}
    if not isinstance(params, dict):
        return ""
    for key in ("path", "pattern", "file", "target", "query", "command"):
        value = params.get(key)
        if value:
            return str(value)
    return ""


def _visible_text(text: str) -> str:
    start = text.find("<user_query>")
    end = text.find("</user_query>")
    if start != -1 and end > start:
        return text[start + len("<user_query>") : end].strip()
    return text


def _messages_from_transcripts(chat: Chat) -> list[ChatMessage]:
    messages: list[ChatMessage] = []
    files: list[Path] = []
    for directory in chat.transcript_paths:
        files.extend(
            path
            for path in sorted(directory.glob("*.jsonl"))
            if path.is_file()
        )
    for path in files:
        try:
            lines = path.read_text(errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            role = item.get("role")
            if role not in {"user", "assistant"}:
                continue
            text = _transcript_text(item)
            if not text:
                continue
            if role == "user":
                text = _visible_text(text)
            messages.append(ChatMessage(role=role, text=text))
    return messages


def _transcript_text(item: dict) -> str:
    message = item.get("message") or {}
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, list):
        parts = [
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        ]
        return "\n".join(part for part in parts if part).strip()
    if isinstance(content, str):
        return content.strip()
    return ""
