from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from cursor_cleaner import __version__

GC_BLOBS_HINT = (
    "Deleted chats leave orphaned agentKv:blob rows in state.vscdb "
    "(tool results, diffs, and other payloads keyed by hash, not chat id). "
    "After Cursor is quit, run Developer: GC Agent KV Blobs from the "
    "Command Palette (Cmd+Shift+P) to reclaim that space. It does not "
    "remove chats; it only garbage-collects unreferenced blobs."
)
from cursor_cleaner.store import (
    SORT_KEYS,
    CursorPaths,
    aggregate_stats,
    backup_chats,
    cursor_is_running,
    default_backup_dir,
    delete_chats,
    format_bytes,
    format_conversation,
    format_stats,
    list_chats,
    load_conversation,
    summarize_repos,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cursor-cleaner",
        description="Find, back up, and delete Cursor chats on this Mac.",
        epilog=(
            "Quit Cursor before deleting. After a large cleanup, run "
            "'Developer: GC Agent KV Blobs' from the Command Palette to reclaim "
            "orphaned blob space."
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command")

    list_p = sub.add_parser("list", help="Show chats (archived only by default)")
    _add_filters(list_p)
    list_p.add_argument(
        "--all",
        action="store_true",
        help="Include active chats, not only archived",
    )
    list_p.add_argument(
        "--sort",
        choices=SORT_KEYS,
        default="updated",
        help="Sort by this field (default: updated, newest first)",
    )
    list_p.add_argument(
        "--reverse",
        action="store_true",
        help="Flip the default sort direction",
    )
    list_p.add_argument("--json", action="store_true", help="Print JSON")
    list_p.add_argument(
        "--repos",
        action="store_true",
        help="List unique repos instead of chats",
    )

    backup_p = sub.add_parser("backup", help="Write a copy of selected chats")
    _add_filters(backup_p)
    backup_p.add_argument(
        "--all",
        action="store_true",
        help="Include active chats, not only archived",
    )
    backup_p.add_argument(
        "--dest",
        type=Path,
        help="Backup directory (default: ~/cursor-cleaner-backups/<timestamp>)",
    )

    delete_p = sub.add_parser(
        "delete",
        help="Delete chats (archived only unless --id or --repo)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=GC_BLOBS_HINT,
    )
    _add_filters(delete_p)
    delete_p.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be deleted without changing anything",
    )
    delete_p.add_argument(
        "--yes",
        action="store_true",
        help="Actually delete (refused unless Cursor is quit, or --force)",
    )
    delete_p.add_argument(
        "--force",
        action="store_true",
        help="Delete even if Cursor is still running (history may come back)",
    )
    delete_p.add_argument(
        "--backup",
        nargs="?",
        const="__default__",
        metavar="DIR",
        help="Back up selected chats before deleting (default: ~/cursor-cleaner-backups/<timestamp>)",
    )
    delete_p.add_argument(
        "--vacuum",
        action="store_true",
        help="VACUUM the database after delete to reclaim file space",
    )

    stats_p = sub.add_parser("stats", help="Show model, line, and token totals")
    _add_filters(stats_p)
    stats_p.add_argument(
        "--archived",
        action="store_true",
        help="Archived chats only (default: all chats)",
    )
    stats_p.add_argument("--json", action="store_true", help="Print JSON")

    view_p = sub.add_parser("view", help="Print one chat")
    view_p.add_argument("composer_id", metavar="ID", help="Composer id from list")
    view_p.add_argument("--json", action="store_true", help="Print JSON")
    view_p.add_argument(
        "--thinking",
        action="store_true",
        help="Include thinking bubbles",
    )
    return parser


def _add_filters(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--workspace",
        help="Only chats whose workspace path, id, or repo contains this text",
    )
    parser.add_argument(
        "--repo",
        help="Only chats whose repo name matches exactly (folder name)",
    )
    parser.add_argument(
        "--older-than",
        type=int,
        metavar="DAYS",
        help="Only chats last updated more than DAYS ago",
    )
    parser.add_argument(
        "--id",
        action="append",
        dest="ids",
        metavar="COMPOSER_ID",
        help="This chat id, archived or not (repeatable)",
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    command = args.command or "list"
    paths = CursorPaths()
    if command == "view":
        return _cmd_view(args, paths)
    ids = getattr(args, "ids", None)
    repo = getattr(args, "repo", None)
    archived_only = not ids and not getattr(args, "all", False)
    if command == "delete" and repo:
        archived_only = False
    if command == "stats":
        archived_only = bool(getattr(args, "archived", False))

    try:
        chats = list_chats(
            paths,
            workspace=getattr(args, "workspace", None),
            repo=repo,
            older_than_days=getattr(args, "older_than", None),
            ids=ids,
            archived_only=archived_only,
            sort=getattr(args, "sort", "updated"),
            reverse=getattr(args, "reverse", False),
            sizes=command != "stats" and not getattr(args, "repos", False),
        )
    except (FileNotFoundError, RuntimeError) as exc:
        print(exc, file=sys.stderr)
        return 2

    if ids:
        found = {chat.composer_id for chat in chats}
        missing = [chat_id for chat_id in ids if chat_id not in found]
        if missing:
            print("Chat not found: " + ", ".join(missing), file=sys.stderr)
            return 2

    if command == "list":
        if getattr(args, "repos", False):
            _print_repos(chats, as_json=getattr(args, "json", False))
        elif getattr(args, "json", False):
            print(json.dumps([_chat_json(chat) for chat in chats], indent=2))
        else:
            _print_table(chats)
        return 0

    if command == "stats":
        stats = aggregate_stats(chats)
        if getattr(args, "json", False):
            print(
                json.dumps(
                    {
                        "chats": stats.chats,
                        "archived": stats.archived,
                        "lines_added": stats.lines_added,
                        "lines_removed": stats.lines_removed,
                        "files_changed": stats.files_changed,
                        "tokens": stats.tokens,
                        "models": stats.models,
                        "repos": stats.repos,
                    },
                    indent=2,
                )
            )
        else:
            print(format_stats(stats), end="")
        return 0

    if not chats:
        print("No chats matched.")
        return 0

    _print_table(chats)

    if command == "backup":
        dest = args.dest if args.dest is not None else default_backup_dir()
        result = backup_chats(paths, chats, dest)
        print(f"Backed up {result.chats} chat(s) to {result.path}")
        return 0

    if args.dry_run or not args.yes:
        print("Dry run. Re-run with --yes to delete. Quit Cursor first.")
        if args.backup:
            print("A backup would be written first.")
        return 0

    if cursor_is_running() and not args.force:
        print(
            "Cursor is running. Quit it fully, then re-run, or pass --force.",
            file=sys.stderr,
        )
        return 3

    backup_dir = None
    if args.backup:
        backup_dir = default_backup_dir() if args.backup == "__default__" else Path(args.backup)

    try:
        result = delete_chats(paths, chats, vacuum=args.vacuum, backup_dir=backup_dir)
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        return 2
    print(
        f"Deleted {result.chats} chat(s): "
        f"{result.header_rows} headers, {result.kv_rows} kv rows, "
        f"{result.search_rows} search rows, {result.transcript_dirs} transcripts "
        f"({format_bytes(result.bytes_removed)})."
    )
    if result.backup_path:
        print(f"Backup: {result.backup_path}")
    if result.vacuumed:
        print("Database vacuumed.")
    print(GC_BLOBS_HINT)
    return 0


def _cmd_view(args, paths: CursorPaths) -> int:
    try:
        chat, messages = load_conversation(paths, args.composer_id)
    except (FileNotFoundError, RuntimeError) as exc:
        print(exc, file=sys.stderr)
        return 2
    if args.json:
        print(
            json.dumps(
                {
                    **_chat_json(chat),
                    "messages": [
                        {
                            "role": message.role,
                            "text": message.text,
                            "tool_name": message.tool_name,
                        }
                        for message in messages
                    ],
                },
                indent=2,
            )
        )
        return 0
    print(format_conversation(chat, messages, thinking=args.thinking), end="")
    return 0


def _chat_json(chat) -> dict:
    return {
        "id": chat.composer_id,
        "title": chat.title,
        "workspace_id": chat.workspace_id,
        "workspace": chat.workspace_path,
        "repo": chat.repo,
        "updated_at": chat.updated_at.isoformat(timespec="seconds"),
        "size_bytes": chat.size_bytes,
        "is_archived": chat.is_archived,
        "subcomposer_ids": chat.subcomposer_ids,
    }


def _print_repos(chats, *, as_json: bool) -> None:
    rows = summarize_repos(chats)
    if as_json:
        print(json.dumps(rows, indent=2))
        return
    if not rows:
        print("No chats found.")
        return
    print(f"{'REPO':<24} {'CHATS':>5} {'ARCH':>5}")
    for row in rows:
        repo = str(row["repo"])
        if len(repo) > 24:
            repo = repo[:23] + "…"
        print(f"{repo:<24} {row['chats']:>5} {row['archived']:>5}")
    print()
    print(f"{len(rows)} repo(s), {sum(int(row['chats']) for row in rows)} chat(s)")


def _print_table(chats) -> None:
    if not chats:
        print("No chats found.")
        return
    print(f"{'ARCH':<5} {'UPDATED':<17} {'SIZE':>8}  {'REPO':<16}  {'ID':<36}  TITLE")
    for chat in chats:
        title = chat.title.replace("\n", " ")
        repo = chat.repo
        if len(repo) > 16:
            repo = repo[:15] + "…"
        print(
            f"{'yes' if chat.is_archived else 'no':<5} "
            f"{chat.updated_at.strftime('%Y-%m-%d %H:%M'):<17} "
            f"{format_bytes(chat.size_bytes):>8}  "
            f"{repo:<16}  "
            f"{chat.composer_id:<36}  {title}"
        )
    archived = sum(1 for chat in chats if chat.is_archived)
    print()
    print(
        f"{len(chats)} chat(s) ({archived} archived), "
        f"{format_bytes(sum(c.size_bytes for c in chats))}"
    )
