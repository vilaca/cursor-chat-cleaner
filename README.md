# cursor-cleaner

CLI to list, view, back up, and delete Cursor chats on macOS.

Requires **macOS** and **Python 3.10+**. No extra packages.

## Create the environment

From the repo root:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
python -m pip install -e .
```

Check it:

```bash
cursor-cleaner --help
```

Later sessions:

```bash
source .venv/bin/activate
```

To leave the env: `deactivate`.

Without installing, you can run:

```bash
PYTHONPATH=src python3 -m cursor_cleaner --help
```

## Commands

`list` shows **archived** chats unless you pass `--all` or `--id`.

```bash
cursor-cleaner list
cursor-cleaner list --all
cursor-cleaner list --repo e1f --all
cursor-cleaner list --id <composer-id>
cursor-cleaner list --sort size
cursor-cleaner list --sort title --reverse
cursor-cleaner list --sort repo
cursor-cleaner list --older-than 30
cursor-cleaner list --repos
cursor-cleaner list --repos --all
cursor-cleaner list --json
```

`--sort` accepts `updated` (default, newest first), `created`, `size` (largest first), `title`, `repo`, or `workspace`. `--reverse` flips that default.

`stats` totals models, line churn, files, and context tokens (all chats unless `--archived`).

```bash
cursor-cleaner stats
cursor-cleaner stats --repo e1f
cursor-cleaner stats --archived
cursor-cleaner stats --json
```

`view` prints one chat (user/assistant text and compact tool lines). Thinking is hidden unless `--thinking`.

```bash
cursor-cleaner view <composer-id>
cursor-cleaner view <composer-id> --thinking
cursor-cleaner view <composer-id> --json
```

`backup` writes a copy and leaves the original in place.

```bash
cursor-cleaner backup
cursor-cleaner backup --id <composer-id> --dest ~/Desktop/chat-backup
cursor-cleaner backup --repo e1f --dest ~/Desktop/e1f-chats
```

`delete` removes **archived** chats only, unless you pass `--id` or `--repo` (those include active chats in the match).

```bash
cursor-cleaner delete --dry-run
cursor-cleaner delete --yes --backup
cursor-cleaner delete --id <composer-id> --yes --backup ~/Desktop/chat-backup
cursor-cleaner delete --repo e1f --dry-run
cursor-cleaner delete --repo e1f --yes --backup
cursor-cleaner delete --older-than 30 --yes --backup
```

`--backup` without a path uses `~/cursor-cleaner-backups/<timestamp>`. Each backup has `manifest.json`, `chats.sqlite`, optional `search.json`, and copied transcripts.

`--vacuum` runs SQLite `VACUUM` after delete so the database file can shrink. That needs roughly as much free disk as the current DB size.

## Safety

- **Quit Cursor** before `--yes`. An open session can rewrite history after you delete.
- `--yes` is required to change anything. Without it, delete is a dry-run.
- `--force` deletes while Cursor is still running. History may come back.
- Preview first: `list` / `view` / `delete --dry-run`.
- `delete` refuses to write if `state.vscdb` is missing `composerHeaders` (or its `isArchived` columns) or if `composer.composerHeaders.tableGateEnabled` is not true. App version is not used as the lock.

## After a delete: Developer: GC Agent KV Blobs

Deleting a chat does **not** remove leftover `agentKv:blob:<hash>` rows in `state.vscdb`. Those hold tool results, diffs, and other payloads keyed by hash, not by chat id, so they become orphans.

This tool cannot safely garbage-collect them. After Cursor is quit and delete finishes:

1. Open Cursor.
2. `Cmd+Shift+P`
3. Run **Developer: GC Agent KV Blobs**

That command does not remove chats from the sidebar. It only drops unreferenced blobs so `state.vscdb` can shrink.

## Where chats live

| Location | What |
| --- | --- |
| `~/Library/Application Support/Cursor/User/globalStorage/state.vscdb` | Chat headers, messages, checkpoints |
| `~/Library/Application Support/Cursor/User/globalStorage/conversation-search.db` | Search index |
| `~/Library/Application Support/Cursor/User/workspaceStorage/<id>/workspace.json` | Maps a workspace id to a folder (repo name) |
| `~/.cursor/projects/*/agent-transcripts/<composer-id>/` | Plain transcript files |

Do not delete `state.vscdb` as a file. That can break Cursor history.

## Tests

```bash
source .venv/bin/activate
PYTHONPATH=src python -m unittest discover -s tests -v
```
