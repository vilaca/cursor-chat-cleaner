from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("cursor-chat-cleaner")
except PackageNotFoundError:
    # Source tree without an installed distribution (e.g. PYTHONPATH=src).
    import re
    from pathlib import Path

    _pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    _match = re.search(
        r'(?m)^version\s*=\s*"([^"]+)"',
        _pyproject.read_text(encoding="utf-8"),
    )
    if _match is None:
        raise RuntimeError(f"version not found in {_pyproject}") from None
    __version__ = _match.group(1)
