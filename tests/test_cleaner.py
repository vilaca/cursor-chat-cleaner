from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from cursor_cleaner.store import (
    CursorPaths,
    aggregate_stats,
    backup_chats,
    delete_chats,
    format_conversation,
    list_chats,
    load_conversation,
    summarize_repos,
)


SCHEMA = """
CREATE TABLE ItemTable (key TEXT UNIQUE ON CONFLICT REPLACE, value BLOB);
CREATE TABLE cursorDiskKV (key TEXT UNIQUE ON CONFLICT REPLACE, value BLOB);
CREATE TABLE composerHeaders (
    composerId TEXT PRIMARY KEY,
    workspaceId TEXT,
    createdAt INTEGER,
    lastUpdatedAt INTEGER,
    isArchived INTEGER,
    isSubagent INTEGER,
    recency INTEGER,
    checkpointAt INTEGER,
    value TEXT
);
"""

SEARCH_SCHEMA = """
CREATE TABLE conversations (
    fts_rowid INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    scope TEXT NOT NULL,
    id TEXT NOT NULL,
    title TEXT NOT NULL,
    branches TEXT NOT NULL,
    updated_at INTEGER NOT NULL,
    is_archived INTEGER NOT NULL
);
CREATE VIRTUAL TABLE conversation_fts USING fts5(title, body, branches);
CREATE TABLE conversation_search_candidates (
    id TEXT PRIMARY KEY,
    updated_at INTEGER NOT NULL
);
"""


def _now_ms(days_ago: int = 0) -> int:
    return int((datetime.now() - timedelta(days=days_ago)).timestamp() * 1000)


class CleanerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        user = root / "User"
        projects = root / "projects"
        (user / "globalStorage").mkdir(parents=True)
        (user / "workspaceStorage" / "ws1").mkdir(parents=True)
        (user / "workspaceStorage" / "ws1" / "workspace.json").write_text(
            json.dumps({"folder": "file:///Users/vilaca/work/e1f"})
        )
        self.paths = CursorPaths(user_dir=user, projects_dir=projects)
        self.con = sqlite3.connect(self.paths.global_db)
        self.con.executescript(SCHEMA)
        self._add_chat(
            "arch-1",
            "Old archived",
            archived=True,
            days_ago=10,
            workspace="ws1",
            model="grok-4.6",
            lines_added=100,
            lines_removed=10,
            files_changed=3,
            tokens=1000,
        )
        self._add_chat(
            "live-1",
            "Active chat",
            archived=False,
            days_ago=0,
            workspace="ws1",
            model="gpt-5.6-sol",
            lines_added=20,
            lines_removed=2,
            files_changed=1,
            tokens=200,
        )
        self._add_chat(
            "arch-2",
            "Recent archived",
            archived=True,
            days_ago=1,
            workspace="empty",
            model="claude-opus-4-7",
            lines_added=5,
            lines_removed=1,
            files_changed=0,
            tokens=50,
        )
        self.con.commit()

        search = sqlite3.connect(self.paths.search_db)
        search.executescript(SEARCH_SCHEMA)
        search.execute(
            "INSERT INTO conversations VALUES (1,'local','','arch-1','Old archived','[]',1,1)"
        )
        search.execute("INSERT INTO conversation_fts(rowid, title, body, branches) VALUES (1,'Old archived','hello','')")
        search.execute("INSERT INTO conversation_search_candidates VALUES ('arch-1', 1)")
        search.commit()
        search.close()

        transcript = projects / "Users-e1f" / "agent-transcripts" / "arch-1"
        transcript.mkdir(parents=True)
        (transcript / "arch-1.jsonl").write_text('{"role":"user"}\n')

    def tearDown(self) -> None:
        self.con.close()
        self.tmp.cleanup()

    def _add_chat(
        self,
        cid: str,
        title: str,
        *,
        archived: bool,
        days_ago: int,
        workspace: str,
        model: str = "",
        lines_added: int = 0,
        lines_removed: int = 0,
        files_changed: int = 0,
        tokens: int = 0,
    ) -> None:
        ts = _now_ms(days_ago)
        header = {"name": title, "composerId": cid, "isArchived": archived}
        self.con.execute(
            "INSERT INTO composerHeaders VALUES (?,?,?,?,?,?,?,?,?)",
            (cid, workspace, ts, ts, int(archived), 0, ts, ts, json.dumps(header)),
        )
        body = {
            "name": title,
            "composerId": cid,
            "subComposerIds": [],
            "modelConfig": {"modelName": model} if model else {},
            "totalLinesAdded": lines_added,
            "totalLinesRemoved": lines_removed,
            "filesChangedCount": files_changed,
            "contextTokensUsed": tokens,
        }
        self.con.execute(
            "INSERT INTO cursorDiskKV VALUES (?,?)",
            (f"composerData:{cid}", json.dumps(body)),
        )
        self.con.execute(
            "INSERT INTO cursorDiskKV VALUES (?,?)",
            (f"bubbleId:{cid}:bubble-1", f"message for {title}"),
        )
        self.con.execute(
            "INSERT INTO ItemTable VALUES (?,?)",
            (f"glass/cursor/{cid}", "1"),
        )
        self.con.execute(
            "INSERT OR REPLACE INTO ItemTable VALUES (?,?)",
            (
                "composer.composerHeaders",
                json.dumps(
                    {
                        "allComposers": [
                            {"composerId": "arch-1", "isArchived": True},
                            {"composerId": "live-1", "isArchived": False},
                            {"composerId": "arch-2", "isArchived": True},
                        ]
                    }
                ),
            ),
        )

    def test_list_defaults_to_archived(self) -> None:
        chats = list_chats(self.paths)
        self.assertEqual({c.composer_id for c in chats}, {"arch-1", "arch-2"})

    def test_list_id_includes_active(self) -> None:
        chats = list_chats(self.paths, ids=["live-1"], archived_only=False)
        self.assertEqual(len(chats), 1)
        self.assertEqual(chats[0].title, "Active chat")
        self.assertFalse(chats[0].is_archived)

    def test_list_all(self) -> None:
        chats = list_chats(self.paths, archived_only=False)
        self.assertEqual(len(chats), 3)

    def test_older_than(self) -> None:
        chats = list_chats(self.paths, older_than_days=5)
        self.assertEqual([c.composer_id for c in chats], ["arch-1"])

    def test_sort_updated_newest_first(self) -> None:
        chats = list_chats(self.paths)
        self.assertEqual([c.composer_id for c in chats], ["arch-2", "arch-1"])

    def test_sort_updated_reverse_oldest_first(self) -> None:
        chats = list_chats(self.paths, reverse=True)
        self.assertEqual([c.composer_id for c in chats], ["arch-1", "arch-2"])

    def test_sort_title(self) -> None:
        chats = list_chats(self.paths, sort="title")
        self.assertEqual([c.title for c in chats], ["Old archived", "Recent archived"])

    def test_sort_size_largest_first(self) -> None:
        chats = list_chats(self.paths, sort="size")
        self.assertGreater(chats[0].size_bytes, chats[1].size_bytes)
        self.assertEqual(chats[0].composer_id, "arch-1")

    def test_sort_workspace(self) -> None:
        chats = list_chats(self.paths, sort="workspace")
        self.assertEqual([c.workspace_id for c in chats], ["ws1", "empty"])

    def test_sort_repo(self) -> None:
        chats = list_chats(self.paths, sort="repo")
        self.assertEqual([c.repo for c in chats], ["e1f", "empty"])

    def test_repo_from_workspace_path(self) -> None:
        chats = list_chats(self.paths, ids=["arch-1"])
        self.assertEqual(chats[0].repo, "e1f")

    def test_list_repo_archived_only(self) -> None:
        chats = list_chats(self.paths, repo="e1f")
        self.assertEqual([c.composer_id for c in chats], ["arch-1"])

    def test_list_repos(self) -> None:
        rows = summarize_repos(list_chats(self.paths, sizes=False))
        self.assertEqual([row["repo"] for row in rows], ["e1f", "empty"])
        self.assertEqual(rows[0]["chats"], 1)
        self.assertEqual(rows[0]["archived"], 1)
        all_rows = summarize_repos(list_chats(self.paths, archived_only=False, sizes=False))
        by_repo = {row["repo"]: row for row in all_rows}
        self.assertEqual(by_repo["e1f"]["chats"], 2)
        self.assertEqual(by_repo["e1f"]["archived"], 1)

    def test_list_repo_all(self) -> None:
        chats = list_chats(self.paths, repo="E1F", archived_only=False)
        self.assertEqual({c.composer_id for c in chats}, {"arch-1", "live-1"})

    def test_delete_archived_leaves_active(self) -> None:
        chats = list_chats(self.paths)
        delete_chats(self.paths, chats)
        remaining = list_chats(self.paths, archived_only=False)
        self.assertEqual([c.composer_id for c in remaining], ["live-1"])
        kv = self._kv_keys()
        self.assertIn("composerData:live-1", kv)
        self.assertNotIn("composerData:arch-1", kv)
        cache = json.loads(
            self.con.execute(
                "SELECT value FROM ItemTable WHERE key='composer.composerHeaders'"
            ).fetchone()[0]
        )
        self.assertEqual([c["composerId"] for c in cache["allComposers"]], ["live-1"])

    def test_delete_all_from_repo(self) -> None:
        chats = list_chats(self.paths, repo="e1f", archived_only=False)
        self.assertEqual({c.composer_id for c in chats}, {"arch-1", "live-1"})
        delete_chats(self.paths, chats)
        remaining = list_chats(self.paths, archived_only=False)
        self.assertEqual([c.composer_id for c in remaining], ["arch-2"])
        self.assertEqual(remaining[0].repo, "empty")

    def test_delete_single_active_chat(self) -> None:
        chats = list_chats(self.paths, ids=["live-1"], archived_only=False)
        delete_chats(self.paths, chats)
        remaining = {c.composer_id for c in list_chats(self.paths, archived_only=False)}
        self.assertEqual(remaining, {"arch-1", "arch-2"})
        self.assertNotIn("composerData:live-1", self._kv_keys())

    def test_backup_then_delete(self) -> None:
        dest = Path(self.tmp.name) / "bak"
        chats = list_chats(self.paths, ids=["arch-1"])
        result = delete_chats(self.paths, chats, backup_dir=dest)
        self.assertTrue((result.backup_path / "manifest.json").is_file())
        manifest = json.loads((result.backup_path / "manifest.json").read_text())
        self.assertEqual(manifest["chats"][0]["id"], "arch-1")
        bak = sqlite3.connect(result.backup_path / "chats.sqlite")
        row = bak.execute(
            "SELECT value FROM cursorDiskKV WHERE key='composerData:arch-1'"
        ).fetchone()
        self.assertIsNotNone(row)
        bak.close()
        self.assertTrue((result.backup_path / "transcripts" / "arch-1" / "arch-1.jsonl").is_file())
        self.assertIsNone(
            sqlite3.connect(self.paths.global_db)
            .execute("SELECT 1 FROM composerHeaders WHERE composerId='arch-1'")
            .fetchone()
        )

    def test_backup_command_keeps_source(self) -> None:
        dest = Path(self.tmp.name) / "only-backup"
        chats = list_chats(self.paths, ids=["live-1"], archived_only=False)
        result = backup_chats(self.paths, chats, dest)
        self.assertTrue((result.path / "chats.sqlite").is_file())
        self.assertTrue(
            sqlite3.connect(self.paths.global_db)
            .execute("SELECT 1 FROM composerHeaders WHERE composerId='live-1'")
            .fetchone()
        )

    def test_stats(self) -> None:
        stats = aggregate_stats(list_chats(self.paths, archived_only=False, sizes=False))
        self.assertEqual(stats.chats, 3)
        self.assertEqual(stats.archived, 2)
        self.assertEqual(stats.lines_added, 125)
        self.assertEqual(stats.lines_removed, 13)
        self.assertEqual(stats.files_changed, 4)
        self.assertEqual(stats.tokens, 1250)
        self.assertEqual(stats.models["grok-4.6"], {"chats": 1, "tokens": 1000})
        self.assertEqual(stats.models["gpt-5.6-sol"], {"chats": 1, "tokens": 200})
        self.assertEqual(list(stats.models), ["grok-4.6", "gpt-5.6-sol", "claude-opus-4-7"])
        self.assertEqual(stats.repos["e1f"]["chats"], 2)
        self.assertEqual(stats.repos["e1f"]["lines_added"], 120)
        self.assertEqual(list(stats.repos), ["e1f", "empty"])
        self.assertEqual(
            stats.repos["e1f"]["models"],
            {"grok-4.6": {"chats": 1, "tokens": 1000}, "gpt-5.6-sol": {"chats": 1, "tokens": 200}},
        )

    def test_stats_archived_only(self) -> None:
        stats = aggregate_stats(list_chats(self.paths, archived_only=True, sizes=False))
        self.assertEqual(stats.chats, 2)
        self.assertEqual(stats.models["gpt-5.6-sol"] if "gpt-5.6-sol" in stats.models else 0, 0)
        self.assertNotIn("gpt-5.6-sol", stats.models)

    def test_unknown_id_is_empty(self) -> None:
        self.assertEqual(list_chats(self.paths, ids=["missing"]), [])

    def test_view_bubbles(self) -> None:
        self.con.execute(
            "UPDATE cursorDiskKV SET value = ? WHERE key = 'composerData:arch-2'",
            (
                json.dumps(
                    {
                        "name": "Recent archived",
                        "fullConversationHeadersOnly": [
                            {"bubbleId": "u1"},
                            {"bubbleId": "t1"},
                            {"bubbleId": "a1"},
                        ],
                    }
                ),
            ),
        )
        self.con.execute("DELETE FROM cursorDiskKV WHERE key LIKE 'bubbleId:arch-2:%'")
        self.con.executemany(
            "INSERT INTO cursorDiskKV VALUES (?,?)",
            [
                (
                    "bubbleId:arch-2:u1",
                    json.dumps({"bubbleId": "u1", "type": 1, "text": "hello there"}),
                ),
                (
                    "bubbleId:arch-2:t1",
                    json.dumps(
                        {
                            "bubbleId": "t1",
                            "type": 2,
                            "text": "",
                            "toolFormerData": {
                                "name": "read_file",
                                "params": '{"path":"README.md"}',
                            },
                        }
                    ),
                ),
                (
                    "bubbleId:arch-2:a1",
                    json.dumps({"bubbleId": "a1", "type": 2, "text": "hi back"}),
                ),
                (
                    "bubbleId:arch-2:th1",
                    json.dumps(
                        {
                            "bubbleId": "th1",
                            "type": 2,
                            "text": "",
                            "thinking": {"text": "secret thought"},
                        }
                    ),
                ),
            ],
        )
        self.con.commit()
        chat, messages = load_conversation(self.paths, "arch-2")
        self.assertEqual([m.role for m in messages], ["user", "tool", "assistant", "thinking"])
        rendered = format_conversation(chat, messages)
        self.assertIn("USER", rendered)
        self.assertIn("hello there", rendered)
        self.assertIn("TOOL  read_file", rendered)
        self.assertIn("README.md", rendered)
        self.assertIn("hi back", rendered)
        self.assertNotIn("secret thought", rendered)
        self.assertIn("secret thought", format_conversation(chat, messages, thinking=True))

    def test_view_missing(self) -> None:
        with self.assertRaises(FileNotFoundError):
            load_conversation(self.paths, "nope")

    def _kv_keys(self) -> set[str]:
        self.con.close()
        self.con = sqlite3.connect(self.paths.global_db)
        return {row[0] for row in self.con.execute("SELECT key FROM cursorDiskKV")}


if __name__ == "__main__":
    unittest.main()
