# Security Policy

## Supported versions

Until the first tagged release, security fixes are applied to the latest commit on `main`. After releases begin, this policy will be updated with the supported versions.

## Reporting a vulnerability

Do not open a public issue for a suspected vulnerability.

Use GitHub's private vulnerability reporting form:

https://github.com/vilaca/cursor-chat-cleaner/security/advisories/new

Include:

- A description of the issue and its impact.
- The affected command and options.
- The Cursor and macOS versions involved.
- Minimal reproduction steps using synthetic data where possible.
- Any suggested mitigation or fix.

Reports involving unintended file deletion, traversal outside Cursor storage, exposure of chat or backup content, unsafe handling of malformed databases, or bypasses of confirmation and schema checks are especially important.

The maintainer will acknowledge the report as soon as practical, investigate it privately, and coordinate disclosure after a fix or mitigation is available.

## Scope

This project operates only on local Cursor data. Vulnerabilities in Cursor itself, GitHub, Python, macOS, or other third-party services should be reported to their respective maintainers.
