# Contributing

Contributions are welcome. This project reads and deletes local Cursor chat data, so changes to storage paths, SQLite queries, schema checks, backups, and deletion require extra care.

## Development setup

Requires macOS and Python 3.10 or newer.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

Run the test suite:

```bash
python -m unittest discover -s tests -v
```

The GitHub Actions workflow runs the same suite on Python 3.10 through 3.14.

## Making changes

- Keep runtime dependencies at zero unless a dependency has a clear safety or maintenance benefit.
- Preserve dry-run and explicit-confirmation behavior for destructive commands.
- Fail closed when Cursor's storage schema or filesystem layout is not recognized.
- Add regression tests for every bug fix.
- Use temporary directories and synthetic databases in tests. Never commit real Cursor databases, transcripts, chat content, credentials, or personal paths.
- Update the README when command behavior or safety guidance changes.

For significant changes to Cursor schema handling or deletion behavior, open an issue before investing in an implementation.

## Pull requests

Before submitting:

1. Rebase or merge the latest `main`.
2. Run the full test suite.
3. Confirm `git diff --check` passes.
4. Verify the wheel builds:

   ```bash
   python -m pip wheel . --no-deps --wheel-dir dist
   ```

5. Describe the user-visible behavior, safety implications, and test coverage.

## Security issues

Do not report vulnerabilities in a public issue. Follow the private reporting instructions in [SECURITY.md](SECURITY.md).

## License

By contributing, you agree that your contributions are licensed under the repository's [MIT License](LICENSE).
