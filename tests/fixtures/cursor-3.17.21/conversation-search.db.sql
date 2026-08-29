-- Sanitized schema-only snapshot from Cursor 3.17.21 on macOS.
-- Only tables required by cursor-chat-cleaner are included.
CREATE TABLE conversations (
    fts_rowid INTEGER PRIMARY KEY,
    source TEXT NOT NULL CHECK (source IN ('local', 'cloud-cache')),
    scope TEXT NOT NULL,
    id TEXT NOT NULL,
    title TEXT NOT NULL,
    branches TEXT NOT NULL,
    updated_at INTEGER NOT NULL,
    is_archived INTEGER NOT NULL,
    root_fingerprint TEXT,
    cache_fingerprint TEXT,
    UNIQUE(source, scope, id),
    CHECK (
        (
            source = 'local'
            AND scope = ''
            AND root_fingerprint IS NOT NULL
            AND cache_fingerprint IS NULL
        )
        OR (
            source = 'cloud-cache'
            AND scope <> ''
            AND root_fingerprint IS NULL
        )
    )
);

CREATE VIRTUAL TABLE conversation_fts USING fts5(
    title,
    body,
    branches,
    tokenize = 'unicode61 remove_diacritics 2',
    prefix = '2 3'
);

CREATE TABLE conversation_search_candidates (
    id TEXT PRIMARY KEY,
    updated_at INTEGER NOT NULL
);
