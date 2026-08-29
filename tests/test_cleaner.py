from __future__ import annotations

import json
import sqlite3
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from cursor_chat_cleaner.cli import (
    GC_BLOBS_HINT,
    active_delete_warning,
    build_parser,
    main,
    selection_archived_only,
)
from cursor_chat_cleaner.store import (
    CursorPaths,
    _command_is_cursor_app,
    aggregate_stats,
    backup_chats,
    cursor_is_running,
    delete_chats,
    format_conversation,
    list_chats,
    load_conversation,
    schema_problems,
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
        self.con.execute(
            "INSERT INTO ItemTable VALUES (?,?)",
            ("composer.composerHeaders.tableGateEnabled", "true"),
        )
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

    def test_older_than_rejects_non_positive(self) -> None:
        with self.assertRaises(ValueError):
            list_chats(self.paths, older_than_days=0)
        with self.assertRaises(ValueError):
            list_chats(self.paths, older_than_days=-5)

    def test_older_than_uses_created_when_updated_null(self) -> None:
        self.con.execute(
            "UPDATE composerHeaders SET lastUpdatedAt = NULL WHERE composerId = 'arch-1'"
        )
        self.con.commit()
        chats = list_chats(self.paths, older_than_days=5)
        self.assertEqual([c.composer_id for c in chats], ["arch-1"])
        self.assertGreater(chats[0].updated_at_ms, 0)

    def test_older_than_skips_chats_without_timestamps(self) -> None:
        self.con.execute(
            "UPDATE composerHeaders SET createdAt = NULL, lastUpdatedAt = NULL "
            "WHERE composerId = 'arch-1'"
        )
        self.con.execute(
            "UPDATE cursorDiskKV SET value = ? WHERE key = 'composerData:arch-1'",
            (json.dumps({"name": "Old archived"}),),
        )
        self.con.commit()
        chats = list_chats(self.paths, older_than_days=5)
        self.assertEqual([c.composer_id for c in chats], [])

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

    def test_same_repo_name_different_folders_stay_separate(self) -> None:
        ws2 = self.paths.user_dir / "workspaceStorage" / "ws2"
        ws2.mkdir(parents=True)
        (ws2 / "workspace.json").write_text(
            json.dumps({"folder": "file:///tmp/other/e1f"})
        )
        self._add_chat("arch-3", "Other e1f", archived=True, days_ago=2, workspace="ws2")
        self.con.commit()
        chats = list_chats(self.paths, archived_only=False, sizes=False)
        labels = {row["repo"] for row in summarize_repos(chats)}
        self.assertIn("work/e1f", labels)
        self.assertIn("other/e1f", labels)
        stats = aggregate_stats(chats)
        self.assertIn("work/e1f", stats.repos)
        self.assertIn("other/e1f", stats.repos)
        self.assertEqual(stats.repos["work/e1f"]["chats"], 2)
        self.assertEqual(stats.repos["other/e1f"]["chats"], 1)

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
        self.assertTrue(
            (
                result.backup_path
                / "transcripts"
                / "Users-e1f"
                / "agent-transcripts"
                / "arch-1"
                / "arch-1.jsonl"
            ).is_file()
        )
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

    def test_schema_ok(self) -> None:
        self.assertEqual(schema_problems(self.paths), [])

    def test_delete_refuses_missing_headers_table(self) -> None:
        self.con.execute("DROP TABLE composerHeaders")
        self.con.commit()
        with self.assertRaises(RuntimeError) as raised:
            delete_chats(self.paths, [])
        self.assertIn("missing table composerHeaders", str(raised.exception))

    def test_backup_keeps_duplicate_transcript_dirs(self) -> None:
        other = self.paths.projects_dir / "other-proj" / "agent-transcripts" / "arch-1"
        other.mkdir(parents=True)
        (other / "copy.jsonl").write_text("{}\n")
        result = backup_chats(
            self.paths, list_chats(self.paths, ids=["arch-1"]), Path(self.tmp.name) / "dup-bak"
        )
        self.assertTrue(
            (
                result.path
                / "transcripts"
                / "Users-e1f"
                / "agent-transcripts"
                / "arch-1"
                / "arch-1.jsonl"
            ).is_file()
        )
        self.assertTrue(
            (
                result.path
                / "transcripts"
                / "other-proj"
                / "agent-transcripts"
                / "arch-1"
                / "copy.jsonl"
            ).is_file()
        )

    def test_delete_does_not_follow_traversal_id_outside_transcripts(self) -> None:
        malicious_id = "../../../victim"
        victim = Path(self.tmp.name) / "victim"
        victim.mkdir()
        marker = victim / "keep.txt"
        marker.write_text("keep")
        self._add_chat(
            malicious_id,
            "Malformed id",
            archived=True,
            days_ago=1,
            workspace="ws1",
        )
        self.con.commit()

        chats = list_chats(self.paths, ids=[malicious_id], archived_only=False)
        delete_chats(self.paths, chats)

        self.assertTrue(marker.is_file())

    def test_delete_treats_glob_characters_in_id_as_literal(self) -> None:
        transcript = self.paths.projects_dir / "Users-e1f" / "agent-transcripts" / "arch-1"
        self.assertTrue(transcript.is_dir())
        self._add_chat(
            "*",
            "Glob id",
            archived=True,
            days_ago=1,
            workspace="ws1",
        )
        self.con.commit()

        chats = list_chats(self.paths, ids=["*"], archived_only=False)
        delete_chats(self.paths, chats)

        self.assertTrue(transcript.is_dir())

    def test_delete_includes_subagent_linked_by_parent_id(self) -> None:
        ts = _now_ms(10)
        child = "sub-1"
        header = {
            "name": "Subagent",
            "composerId": child,
            "subagentInfo": {"parentComposerId": "arch-1"},
        }
        self.con.execute(
            "INSERT INTO composerHeaders VALUES (?,?,?,?,?,?,?,?,?)",
            (child, "ws1", ts, ts, 1, 1, ts, ts, json.dumps(header)),
        )
        self.con.execute(
            "INSERT INTO cursorDiskKV VALUES (?,?)",
            (
                f"composerData:{child}",
                json.dumps(
                    {
                        "name": "Subagent",
                        "subComposerIds": [],
                        "subagentInfo": {"parentComposerId": "arch-1"},
                    }
                ),
            ),
        )
        self.con.execute(
            "UPDATE cursorDiskKV SET value = ? WHERE key = 'composerData:arch-1'",
            (json.dumps({"name": "Old archived", "subComposerIds": []}),),
        )
        self.con.commit()
        chats = list_chats(self.paths, ids=["arch-1"])
        self.assertEqual(chats[0].subcomposer_ids, [child])
        delete_chats(self.paths, chats)
        self.assertIsNone(
            sqlite3.connect(self.paths.global_db)
            .execute("SELECT 1 FROM composerHeaders WHERE composerId=?", (child,))
            .fetchone()
        )
        self.assertNotIn(f"composerData:{child}", self._kv_keys())

    @patch("cursor_chat_cleaner.store._delete_search_rows", side_effect=sqlite3.Error("fts boom"))
    def test_delete_removes_transcripts_if_search_fails(self, _mock) -> None:
        chats = list_chats(self.paths, ids=["arch-1"])
        transcript = self.paths.projects_dir / "Users-e1f" / "agent-transcripts" / "arch-1"
        self.assertTrue(transcript.is_dir())
        with self.assertRaises(RuntimeError) as raised:
            delete_chats(self.paths, chats)
        self.assertIn("cleanup did not finish", str(raised.exception))
        self.assertFalse(transcript.is_dir())
        self.assertIsNone(
            sqlite3.connect(self.paths.global_db)
            .execute("SELECT 1 FROM composerHeaders WHERE composerId='arch-1'")
            .fetchone()
        )

    def test_delete_refuses_missing_search_fts(self) -> None:
        search = sqlite3.connect(self.paths.search_db)
        search.execute("DROP TABLE conversation_fts")
        search.commit()
        search.close()
        self.assertTrue(any("conversation_fts" in item for item in schema_problems(self.paths)))
        with self.assertRaises(RuntimeError):
            delete_chats(self.paths, list_chats(self.paths, ids=["arch-1"]))
        self.assertIsNotNone(
            sqlite3.connect(self.paths.global_db)
            .execute("SELECT 1 FROM composerHeaders WHERE composerId='arch-1'")
            .fetchone()
        )

    def test_delete_refuses_disabled_header_gate(self) -> None:
        self.con.execute(
            "UPDATE ItemTable SET value = ? WHERE key = 'composer.composerHeaders.tableGateEnabled'",
            ("false",),
        )
        self.con.commit()
        with self.assertRaises(RuntimeError) as raised:
            delete_chats(self.paths, [])
        self.assertIn("tableGateEnabled", str(raised.exception))

    def test_delete_refuses_missing_header_gate(self) -> None:
        self.con.execute(
            "DELETE FROM ItemTable WHERE key = 'composer.composerHeaders.tableGateEnabled'"
        )
        self.con.commit()
        with self.assertRaises(RuntimeError) as raised:
            delete_chats(self.paths, [])
        self.assertIn("tableGateEnabled is missing", str(raised.exception))

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

    def test_view_merges_transcript_text_not_in_bubbles(self) -> None:
        self.con.execute(
            "UPDATE cursorDiskKV SET value = ? WHERE key = 'composerData:arch-2'",
            (json.dumps({"name": "Recent archived", "fullConversationHeadersOnly": [{"bubbleId": "u1"}]}),),
        )
        self.con.execute("DELETE FROM cursorDiskKV WHERE key LIKE 'bubbleId:arch-2:%'")
        self.con.execute(
            "INSERT INTO cursorDiskKV VALUES (?,?)",
            (
                "bubbleId:arch-2:u1",
                json.dumps({"bubbleId": "u1", "type": 1, "text": "from bubbles"}),
            ),
        )
        self.con.commit()
        transcript = self.paths.projects_dir / "empty" / "agent-transcripts" / "arch-2"
        transcript.mkdir(parents=True)
        (transcript / "arch-2.jsonl").write_text(
            json.dumps(
                {
                    "role": "assistant",
                    "message": {"content": [{"type": "text", "text": "from transcript"}]},
                }
            )
            + "\n"
        )
        _chat, messages = load_conversation(self.paths, "arch-2")
        self.assertEqual([m.text for m in messages], ["from bubbles", "from transcript"])

    def test_view_includes_subagent_thread(self) -> None:
        ts = _now_ms(1)
        child = "sub-view"
        self.con.execute(
            "INSERT INTO composerHeaders VALUES (?,?,?,?,?,?,?,?,?)",
            (
                child,
                "empty",
                ts,
                ts,
                1,
                1,
                ts,
                ts,
                json.dumps(
                    {
                        "name": "Child agent",
                        "subagentInfo": {"parentComposerId": "arch-2"},
                    }
                ),
            ),
        )
        self.con.execute(
            "UPDATE cursorDiskKV SET value = ? WHERE key = 'composerData:arch-2'",
            (
                json.dumps(
                    {
                        "name": "Recent archived",
                        "subComposerIds": [child],
                        "fullConversationHeadersOnly": [{"bubbleId": "u1"}],
                    }
                ),
            ),
        )
        self.con.execute("DELETE FROM cursorDiskKV WHERE key LIKE 'bubbleId:arch-2:%'")
        self.con.execute(
            "INSERT INTO cursorDiskKV VALUES (?,?)",
            (
                "bubbleId:arch-2:u1",
                json.dumps({"bubbleId": "u1", "type": 1, "text": "parent says"}),
            ),
        )
        self.con.execute(
            "INSERT INTO cursorDiskKV VALUES (?,?)",
            (
                f"composerData:{child}",
                json.dumps(
                    {
                        "name": "Child agent",
                        "fullConversationHeadersOnly": [{"bubbleId": "c1"}],
                    }
                ),
            ),
        )
        self.con.execute(
            "INSERT INTO cursorDiskKV VALUES (?,?)",
            (
                f"bubbleId:{child}:c1",
                json.dumps({"bubbleId": "c1", "type": 2, "text": "child says"}),
            ),
        )
        self.con.commit()
        chat, messages = load_conversation(self.paths, "arch-2")
        self.assertIn(child, chat.subcomposer_ids)
        self.assertEqual(
            [(m.role, m.text) for m in messages],
            [("user", "parent says"), ("subagent", "Child agent"), ("assistant", "child says")],
        )
        self.assertIn("SUBAGENT  Child agent", format_conversation(chat, messages))

    def test_workspace_json_workspace_key(self) -> None:
        ws = self.paths.user_dir / "workspaceStorage" / "ws-multi"
        ws.mkdir(parents=True)
        (ws / "workspace.json").write_text(
            json.dumps({"workspace": "file:///Users/vilaca/work/multi-root"})
        )
        self._add_chat("arch-ws", "Multi", archived=True, days_ago=8, workspace="ws-multi")
        self.con.commit()
        chats = list_chats(self.paths, ids=["arch-ws"], sizes=False)
        self.assertEqual(chats[0].repo, "multi-root")

    def _cli(self, argv: list[str]) -> tuple[int, str]:
        out = StringIO()
        err = StringIO()
        args = [
            "--user-dir",
            str(self.paths.user_dir),
            "--projects-dir",
            str(self.paths.projects_dir),
            *argv,
        ]
        with patch("sys.stdout", out), patch("sys.stderr", err):
            code = main(args)
        return code, out.getvalue() + err.getvalue()

    def test_main_list_defaults_to_archived(self) -> None:
        code, text = self._cli(["list"])
        self.assertEqual(code, 0)
        self.assertIn("arch-1", text)
        self.assertNotIn("live-1", text)

    def test_main_delete_repo_warns_about_active(self) -> None:
        code, text = self._cli(["delete", "--repo", "e1f"])
        self.assertEqual(code, 0)
        self.assertIn("live-1", text)
        self.assertIn("active chat(s) in this selection will be deleted", text)
        self.assertIn("Dry run", text)

    def test_main_older_than_rejects_negative(self) -> None:
        with self.assertRaises(SystemExit):
            self._cli(["list", "--older-than", "-1"])

    def test_cursor_helper_counts_as_running(self) -> None:
        self.assertTrue(
            _command_is_cursor_app(
                "/Applications/Cursor.app/Contents/Frameworks/Cursor Helper.app/"
                "Contents/MacOS/Cursor Helper --type=gpu-process"
            )
        )
        self.assertTrue(
            _command_is_cursor_app("/Applications/Cursor.app/Contents/MacOS/Cursor")
        )
        self.assertTrue(
            _command_is_cursor_app(
                "/Applications/Cursor Nightly.app/Contents/MacOS/Cursor Nightly"
            )
        )
        self.assertFalse(
            _command_is_cursor_app(
                "/System/Library/PrivateFrameworks/TextInputUIMacHelper.framework/"
                "Versions/A/XPCServices/CursorUIViewService.xpc/Contents/MacOS/"
                "CursorUIViewService"
            )
        )
        self.assertFalse(_command_is_cursor_app("/Applications/Google Chrome.app/"))

    @patch("cursor_chat_cleaner.store.subprocess.run")
    def test_cursor_is_running_reads_ps_list(self, run) -> None:
        run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=(
                "/usr/libexec/syspolicyd\n"
                "/Applications/Cursor.app/Contents/MacOS/Cursor\n"
            ),
            stderr="",
        )
        self.assertTrue(cursor_is_running())

    @patch("cursor_chat_cleaner.store.subprocess.run")
    def test_cursor_is_running_false_when_only_unrelated(self, run) -> None:
        run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="/System/Library/.../CursorUIViewService\n",
            stderr="",
        )
        self.assertFalse(cursor_is_running())

    @patch("cursor_chat_cleaner.store.subprocess.run")
    def test_cursor_is_running_fail_closed_if_ps_fails(self, run) -> None:
        run.return_value = subprocess.CompletedProcess(
            args=[], returncode=3, stdout="", stderr="Cannot get process list"
        )
        self.assertTrue(cursor_is_running())

    def _kv_keys(self) -> set[str]:
        self.con.close()
        self.con = sqlite3.connect(self.paths.global_db)
        return {row[0] for row in self.con.execute("SELECT key FROM cursorDiskKV")}


class SelectionTest(unittest.TestCase):
    def _args(self, argv: list[str]):
        return build_parser().parse_args(argv)

    def test_backup_and_delete_repo_include_active(self) -> None:
        self.assertFalse(selection_archived_only("backup", self._args(["backup", "--repo", "e1f"])))
        self.assertFalse(selection_archived_only("delete", self._args(["delete", "--repo", "e1f"])))

    def test_backup_without_repo_is_archived_only(self) -> None:
        self.assertTrue(selection_archived_only("backup", self._args(["backup"])))
        self.assertTrue(selection_archived_only("list", self._args(["list"])))

    def test_backup_all_includes_active(self) -> None:
        self.assertFalse(selection_archived_only("backup", self._args(["backup", "--all"])))

    def test_active_delete_warning(self) -> None:
        archived = type("C", (), {"is_archived": True})()
        active = type("C", (), {"is_archived": False})()
        self.assertIsNone(active_delete_warning([archived, archived]))
        self.assertEqual(
            active_delete_warning([archived, active, active]),
            "2 active chat(s) in this selection will be deleted, not only archived.",
        )

    def test_gc_hint_mentions_hash_keyed_leftovers(self) -> None:
        self.assertIn("agentKv:blob", GC_BLOBS_HINT)
        self.assertIn("composer.content", GC_BLOBS_HINT)
        self.assertIn("inlineDiff", GC_BLOBS_HINT)


if __name__ == "__main__":
    unittest.main()
