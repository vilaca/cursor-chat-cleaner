# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.1] - 2026-09-03

### Fixed

- Show Y/N instead of 0/1 for the ARCH column in `stats` and `repos` output.
- Only treat the main Cursor editor process as running (not helper processes).

## [0.1.0] - 2026-08-29

### Added

- Commands to list, inspect, summarize, back up, delete, and clean up Cursor
  chats.
- Filtering by chat ID, repository, workspace, archive status, and age.
- Human-readable and JSON output for scripting.
- Backup manifests, SQLite exports, search metadata, and transcript copies.
- Recovery command for retrying incomplete search-index or transcript cleanup.
- Support for stable and Nightly storage locations on macOS.
- Schema-only fixtures from Cursor 3.17.21 to detect storage compatibility
  changes.
- Automated tests for Python 3.10 through 3.14 and trusted PyPI publishing.

### Security

- Destructive commands default to dry-run behavior and require explicit
  confirmation.
- Writes fail closed when the expected database schema or feature gate is
  missing.
- The running application is checked again immediately before database writes.
- Transcript operations reject unsafe paths, traversal IDs, and symlinks.
- Database deletion matches known keys precisely and treats wildcard characters
  as literals.
- Backup directories and files use private permissions.
- Partial cleanup is idempotent and can be retried safely.

[Unreleased]: https://github.com/vilaca/cursor-chat-cleaner/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/vilaca/cursor-chat-cleaner/releases/tag/v0.1.0
