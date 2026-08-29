# cursor-chat-cleaner

CLI to list, view, back up, and delete Cursor chats on macOS.

Requires **macOS**. The Homebrew formula installs the required Python runtime.

Tested with Cursor 3.17.21. Cursor's storage is an internal implementation
detail and may change between releases; destructive commands fail closed when
the required schema is not recognized.

This is an independent community project. It is not affiliated with or
endorsed by Anysphere.

## Install

Install with Homebrew:

```bash
brew install vilaca/tap/cursor-chat-cleaner
```

Check it:

```bash
cursor-chat-cleaner --version
```

To use a checkout instead, see [Running from source](running-from-source.md).

## Commands

`list` shows **archived** chats unless you pass `--all` or `--id`.

```bash
cursor-chat-cleaner list
cursor-chat-cleaner list --all
cursor-chat-cleaner list --repo e1f --all
cursor-chat-cleaner list --id <composer-id>
cursor-chat-cleaner list --sort size
cursor-chat-cleaner list --sort title --reverse
cursor-chat-cleaner list --sort repo
cursor-chat-cleaner list --older-than 30
cursor-chat-cleaner --user-dir ~/Library/Application\ Support/Cursor\ Nightly/User list
cursor-chat-cleaner list --repos
cursor-chat-cleaner list --repos --all
cursor-chat-cleaner list --json
```

`--sort` accepts `updated` (default, newest first), `created`, `size` (largest first), `title`, `repo`, or `workspace`. `--reverse` flips that default.

`stats` totals models, line churn, files, and context tokens (all chats unless `--archived`).

```bash
cursor-chat-cleaner stats
cursor-chat-cleaner stats --repo e1f
cursor-chat-cleaner stats --archived
cursor-chat-cleaner stats --json
```

`view` prints one chat (user/assistant text and compact tool lines). Thinking is hidden unless `--thinking`.

```bash
cursor-chat-cleaner view <composer-id>
cursor-chat-cleaner view <composer-id> --thinking
cursor-chat-cleaner view <composer-id> --json
```

`backup` writes a copy and leaves the original in place. It copies **archived** chats only, unless you pass `--id`, `--repo`, or `--all` (`--repo` includes active chats, same as `delete --repo`).

```bash
cursor-chat-cleaner backup
cursor-chat-cleaner backup --id <composer-id> --dest ~/Desktop/chat-backup
cursor-chat-cleaner backup --repo e1f --dest ~/Desktop/e1f-chats
```

`delete` removes **archived** chats only, unless you pass `--id` or `--repo` (those include active chats in the match). If the selection includes active chats, delete prints a warning before the dry-run or `--yes` step.

```bash
cursor-chat-cleaner delete --dry-run
cursor-chat-cleaner delete --yes --backup
cursor-chat-cleaner delete --id <composer-id> --yes --backup ~/Desktop/chat-backup
cursor-chat-cleaner delete --repo e1f --dry-run
cursor-chat-cleaner delete --repo e1f --yes --backup
cursor-chat-cleaner delete --older-than 30 --yes --backup
```

`--backup` without a path uses `~/cursor-chat-cleaner-backups/<timestamp>`. Each backup has `manifest.json`, `chats.sqlite`, optional `search.json`, and copied transcripts. Backup directories are restricted to the current user (`0700`); data files are written as `0600`.

Backups are archival snapshots for inspection and retention. This project does
not provide a restore command, and copying backup data into live Cursor storage
is unsupported.

`--vacuum` runs SQLite `VACUUM` after delete so the database file can shrink. That needs roughly as much free disk as the current DB size.

If the main delete succeeds but search-index or transcript cleanup fails, the error prints the pending chat IDs. Cleanup is idempotent, so it is safe to retry:

```bash
cursor-chat-cleaner cleanup --id <composer-id>
cursor-chat-cleaner cleanup --id <composer-id> --yes
```

## Safety

- **Quit Cursor** before `--yes`. An open session can rewrite history after you delete.
- `--yes` is required to change anything. Without it, delete is a dry-run.
- `--force` deletes while Cursor is still running. History may come back.
- Cursor's process state is checked again immediately before the database write.
- Preview first: `list` / `view` / `delete --dry-run`.
- `delete` refuses to write if `state.vscdb` is missing `composerHeaders` (or its `isArchived` columns) or if `composer.composerHeaders.tableGateEnabled` is not true. App version is not used as the lock.

## After a delete: Developer: GC Agent KV Blobs

Deleting a chat does **not** remove leftover hash-keyed rows in `state.vscdb`: `agentKv:blob:<hash>`, `composer.content.<hash>`, and `inlineDiff:<workspace>:<id>`. Those hold tool results, diffs, and other payloads keyed by hash, not by chat id, so they become orphans.

This tool cannot safely garbage-collect them. After Cursor is quit and delete finishes:

1. Open Cursor.
2. `Cmd+Shift+P`
3. Run **Developer: GC Agent KV Blobs**

That command does not remove chats from the sidebar. It drops unreferenced `agentKv` blobs so `state.vscdb` can shrink. `composer.content` and `inlineDiff` rows may still remain.

## Where chats live

| Location | What |
| --- | --- |
| `~/Library/Application Support/Cursor/User/globalStorage/state.vscdb` | Chat headers, messages, checkpoints |
| `~/Library/Application Support/Cursor/User/globalStorage/conversation-search.db` | Search index |
| `~/Library/Application Support/Cursor/User/workspaceStorage/<id>/workspace.json` | Maps a workspace id to a folder (repo name) |
| `~/.cursor/projects/*/agent-transcripts/<composer-id>/` | Plain transcript files |

Do not delete `state.vscdb` as a file. That can break Cursor history.

## Architecture

- `cli.py` owns command parsing, confirmation, and output.
- `store.py` orchestrates chat reads, backups, deletion, cleanup, and statistics.
- `schema.py` contains the version-sensitive Cursor table and key contract.
- `transcripts.py` is the containment boundary for transcript discovery, sizing, copying, and deletion.

## Tests

```bash
source .venv/bin/activate
PYTHONPATH=src python -m unittest discover -s tests -v
```

The tests run against sanitized, schema-only snapshots from the documented
Cursor versions. These fixtures contain no chat content or other user data.

## License

Licensed under the [MIT License](LICENSE).
