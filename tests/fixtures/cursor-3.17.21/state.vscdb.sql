-- Sanitized schema-only snapshot from Cursor 3.17.21 on macOS.
-- Only tables required by cursor-chat-cleaner are included.
CREATE TABLE ItemTable (
    key TEXT UNIQUE ON CONFLICT REPLACE,
    value BLOB
);

CREATE TABLE cursorDiskKV (
    key TEXT UNIQUE ON CONFLICT REPLACE,
    value BLOB
);

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
